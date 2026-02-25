"""
Smart Lock System — Ring Polling Loop
======================================
Provides polling_loop(), an async function that implements the full motion
event pipeline:

  Ring capture -> YOLO detection -> face recognition (on person crop)
  -> EventStore persistence -> SwitchBot unlock (if face matched)

This module is imported by app.py (FastAPI lifespan). It does NOT contain
any asyncio.run calls — uvicorn owns the event loop via app.py.
"""

import asyncio
import datetime
import logging
import time

from config import Config
from object_detector import ObjectDetector, crop_person_bbox, detections_to_dicts
from event_store import EventStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smart-lock.pipeline")


async def polling_loop(ring, recognizer, switchbot, detector: ObjectDetector, store: EventStore):
    """
    Full motion event pipeline loop.

    Polls Ring for new events at Config.POLL_INTERVAL intervals, then for each
    new event runs YOLO detection, face recognition on any person crop, persists
    the event to EventStore, and dispatches SwitchBot unlock if a known face
    was matched.

    All blocking calls (recognizer.identify, switchbot.unlock) are dispatched
    via run_in_executor to avoid blocking the asyncio event loop.

    Args:
        ring: Authenticated RingClient instance.
        recognizer: FaceRecognizer instance (may have empty known_encodings).
        switchbot: SwitchBotClient instance.
        detector: ObjectDetector instance (YOLO async wrapper).
        store: Initialized EventStore instance.

    Raises:
        asyncio.CancelledError: Propagated on graceful shutdown (re-raised after logging).
    """
    last_unlock_time: float = 0
    loop = asyncio.get_running_loop()

    logger.info(
        f"Polling loop started (interval={Config.POLL_INTERVAL}s, "
        f"camera_id={Config.CAMERA_ID}, cooldown={Config.UNLOCK_COOLDOWN}s)"
    )

    while True:
        try:
            # --- Poll for new Ring event ---
            result = await ring.wait_for_event(Config.POLL_INTERVAL)

            if result is None:
                await asyncio.sleep(2)
                continue

            recording_id, event_kind = result
            recorded_at = datetime.datetime.utcnow().isoformat()

            logger.info(
                f"Processing event (recording_id={recording_id}, kind={event_kind})"
            )

            # --- Cooldown check ---
            elapsed = time.time() - last_unlock_time
            if elapsed < Config.UNLOCK_COOLDOWN:
                logger.info(
                    f"Cooldown active ({int(Config.UNLOCK_COOLDOWN - elapsed)}s remaining), "
                    f"skipping unlock for event {recording_id}"
                )
                # Still capture and store the event, just skip the unlock dispatch
                # (fall through to frame capture and persistence)

            # --- Download recording frame ---
            frame = await ring.capture_frame(recording_id)
            if frame is None:
                logger.warning(
                    f"Could not get frame from recording {recording_id}, skipping"
                )
                continue

            # --- Save thumbnail (synchronous, before DB write) ---
            thumbnail_path = store.save_thumbnail(frame, recorded_at)

            # --- YOLO object detection ---
            detections = await detector.detect(frame)
            detection_dicts = detections_to_dicts(detections)

            # --- Face recognition on best person crop ---
            matched_name = None
            face_confidence = None
            face_distance = None

            person_detections = [d for d in detections if d.label == "person"]

            if person_detections:
                # Pick highest-confidence person detection
                best_person = max(person_detections, key=lambda d: d.confidence)
                person_crop = crop_person_bbox(frame, best_person)

                if person_crop is not None:
                    matched_name = await loop.run_in_executor(
                        None, recognizer.identify, person_crop
                    )
                else:
                    # Degenerate bounding box — fall back to full frame
                    logger.debug(
                        "Person crop returned None (degenerate bbox), "
                        "falling back to full frame for face recognition"
                    )
                    matched_name = await loop.run_in_executor(
                        None, recognizer.identify, frame
                    )
            else:
                logger.debug("No person detected by YOLO — skipping face recognition")
                matched_name = None

            # face_confidence and face_distance: FaceRecognizer.identify() returns
            # name only; storing None per Phase 3 research recommendation.
            # These fields will be populated in a future phase if the recognizer
            # is extended to return distance scores.

            # --- Determine door action and dispatch unlock ---
            unlock_granted = False
            door_action = "none"

            if matched_name is not None:
                logger.info(f"Authorized person detected: {matched_name}")

                # Check cooldown before dispatching unlock
                elapsed = time.time() - last_unlock_time
                if elapsed >= Config.UNLOCK_COOLDOWN:
                    try:
                        success = await loop.run_in_executor(None, switchbot.unlock)
                        if success:
                            last_unlock_time = time.time()
                            unlock_granted = True
                            door_action = "unlocked"
                            logger.info(
                                f"Door unlocked for {matched_name} — welcome home!"
                            )
                        else:
                            logger.error("SwitchBot unlock command returned failure")
                    except Exception as unlock_exc:
                        logger.error(
                            f"SwitchBot unlock failed with exception: {unlock_exc}"
                        )
                else:
                    logger.info(
                        f"Face matched ({matched_name}) but cooldown active "
                        f"({int(Config.UNLOCK_COOLDOWN - elapsed)}s remaining)"
                    )
            else:
                logger.info("Event processed — no authorized face matched")

            # --- Persist event to EventStore ---
            event_id = await store.write_event(
                camera_id=Config.CAMERA_ID,
                recorded_at=recorded_at,
                recording_id=str(recording_id),
                event_type=event_kind,
                person_name=matched_name,
                face_confidence=face_confidence,
                face_distance=face_distance,
                unlock_granted=unlock_granted,
                door_action=door_action,
                thumbnail_path=thumbnail_path,
                detections=detection_dicts,
            )
            logger.info(f"Event persisted: event_id={event_id}")

        except asyncio.CancelledError:
            # CRITICAL: must be caught and re-raised BEFORE except Exception
            # for clean shutdown via polling_task.cancel()
            logger.info("Polling loop cancelled — shutting down cleanly")
            raise

        except Exception as exc:
            logger.exception(f"Unexpected error in polling loop: {exc}")
            await asyncio.sleep(5)

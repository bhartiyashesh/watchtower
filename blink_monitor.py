"""
Smart Lock System — Blink Camera Motion Monitoring
====================================================
Provides blink_polling_loop(), an async function that continuously polls the
Blink camera for motion events and runs the full analysis pipeline:

  Blink refresh -> motion_detected? -> get_snapshot() -> YOLO detection
  -> face recognition (on person crop) -> EventStore persistence
  -> Telegram alert

Mirrors main.py:polling_loop() but uses Blink's motion detection API instead
of Ring events. No SwitchBot unlock — Blink is a secondary surveillance camera.
"""

import asyncio
import datetime
import logging
import time
import zoneinfo

from config import Config
from object_detector import ObjectDetector, crop_person_bbox, detections_to_dicts
from event_store import EventStore

logger = logging.getLogger("smart-lock.blink-monitor")


async def blink_polling_loop(blink, detector: ObjectDetector, recognizer, store: EventStore, alerter=None):
    """
    Blink motion event pipeline loop.

    Polls Blink cloud for motion_detected at BLINK_POLL_INTERVAL intervals,
    then for each new motion event runs YOLO detection, face recognition on
    any person crop, persists to EventStore, and sends Telegram alerts.

    Args:
        blink: Authenticated BlinkClient instance.
        detector: ObjectDetector instance (YOLO async wrapper).
        recognizer: FaceRecognizer instance.
        store: Initialized EventStore instance.
        alerter: Optional TelegramAlerter instance. If None, alerts are skipped.
    """
    last_motion_time: float = 0
    loop = asyncio.get_running_loop()

    logger.info(
        "Blink polling loop started (interval=%ds, cooldown=%ds)",
        Config.BLINK_POLL_INTERVAL,
        Config.BLINK_MOTION_COOLDOWN,
    )

    while True:
        try:
            # --- Poll Blink cloud for motion ---
            await blink.blink.refresh()

            if not blink._camera.motion_detected:
                await asyncio.sleep(Config.BLINK_POLL_INTERVAL)
                continue

            # --- Cooldown check ---
            now = time.monotonic()
            if now - last_motion_time < Config.BLINK_MOTION_COOLDOWN:
                remaining = int(Config.BLINK_MOTION_COOLDOWN - (now - last_motion_time))
                logger.debug(
                    "Blink motion cooldown active (%ds remaining), skipping",
                    remaining,
                )
                await asyncio.sleep(Config.BLINK_POLL_INTERVAL)
                continue

            last_motion_time = now
            recorded_at = datetime.datetime.now(
                zoneinfo.ZoneInfo("America/Chicago")
            ).isoformat()

            logger.info("Blink motion detected — processing pipeline")

            # --- Fetch snapshot ---
            frame = await blink.get_snapshot()
            if frame is None:
                logger.warning("Could not get Blink snapshot, skipping event")
                await asyncio.sleep(Config.BLINK_POLL_INTERVAL)
                continue

            # --- Save thumbnail ---
            thumbnail_path = store.save_thumbnail(frame, recorded_at)

            # --- YOLO object detection ---
            detections = await detector.detect(frame)
            detection_dicts = detections_to_dicts(detections)

            # --- Face recognition on best person crop ---
            matched_name = None
            person_detections = [d for d in detections if d.label == "person"]

            if person_detections:
                best_person = max(person_detections, key=lambda d: d.confidence)
                person_crop = crop_person_bbox(frame, best_person)

                if person_crop is not None:
                    matched_name = await loop.run_in_executor(
                        None, recognizer.identify, person_crop
                    )
                else:
                    logger.debug(
                        "Person crop returned None (degenerate bbox), "
                        "falling back to full frame for face recognition"
                    )
                    matched_name = await loop.run_in_executor(
                        None, recognizer.identify, frame
                    )
            else:
                logger.debug("No person detected by YOLO — skipping face recognition")

            # --- Persist event to EventStore ---
            event_id = await store.write_event(
                camera_id="blink",
                recorded_at=recorded_at,
                recording_id=None,
                event_type="motion",
                person_name=matched_name,
                face_confidence=None,
                face_distance=None,
                unlock_granted=False,
                door_action="none",
                thumbnail_path=thumbnail_path,
                detections=detection_dicts,
            )
            logger.info("Blink event persisted: event_id=%d", event_id)

            # --- Send Telegram alert ---
            if alerter is not None:
                objects_summary = ", ".join(d["label"] for d in detection_dicts) if detection_dicts else "motion"

                if matched_name is None and person_detections:
                    # Unknown person detected
                    caption = (
                        f"Blink: Unknown person detected\n"
                        f"Objects: {objects_summary}\n"
                        f"Time: {recorded_at}"
                    )
                    await alerter.alert_stranger(thumbnail_path, caption)
                elif not person_detections and detection_dicts:
                    # Non-person objects (car, animal, package, etc.)
                    caption = (
                        f"Blink: Motion detected\n"
                        f"Objects: {objects_summary}\n"
                        f"Time: {recorded_at}"
                    )
                    await alerter.alert_blink_motion(thumbnail_path, caption)
                elif not detection_dicts:
                    # Motion detected but YOLO found nothing specific
                    caption = (
                        f"Blink: Motion detected (no objects identified)\n"
                        f"Time: {recorded_at}"
                    )
                    await alerter.alert_blink_motion(thumbnail_path, caption)
                elif matched_name is not None:
                    # Known person — just log, no alert needed
                    logger.info("Blink: recognized %s, no alert needed", matched_name)

            await asyncio.sleep(Config.BLINK_POLL_INTERVAL)

        except asyncio.CancelledError:
            logger.info("Blink polling loop cancelled — shutting down cleanly")
            raise

        except Exception as exc:
            logger.exception("Unexpected error in Blink polling loop: %s", exc)
            await asyncio.sleep(5)

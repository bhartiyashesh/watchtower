"""
ObjectDetector: YOLO11n async wrapper for the Smart Lock Analytics Platform.

This module wraps YOLO11n's synchronous model.predict() behind an async
interface using a single-worker ThreadPoolExecutor. Inference never blocks
the asyncio event loop. It also provides crop_person_bbox() for Phase 3 to
pass cropped person regions to FaceRecognizer.identify() instead of full frames.

Exported symbols:
    ObjectDetector       — async YOLO inference class
    Detection            — dataclass for a single detected object
    crop_person_bbox     — extract a person bounding box as JPEG bytes
    detections_to_dicts  — convert Detection list to EventStore dict format
    RELEVANT_CLASSES     — COCO class ID -> project label mapping
    CONFIDENCE_THRESHOLD — minimum confidence to keep a detection
"""

import asyncio
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PIL import Image
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# COCO class IDs mapped to project-specific labels.
# Only detections whose class is in this dict are returned by detect().
# ---------------------------------------------------------------------------
RELEVANT_CLASSES: dict[int, str] = {
    0: "person",
    2: "car",
    15: "cat",
    16: "dog",
    28: "package",  # COCO "suitcase" class — closest proxy for delivery packages.
                     # Known limitation: flat envelopes and padded mailers may not be detected.
}

# Detections with confidence below this threshold are dropped.
CONFIDENCE_THRESHOLD: float = 0.40


@dataclass
class Detection:
    """A single YOLO detection result for a relevant object class."""

    label: str
    confidence: float
    bbox_x1: float
    bbox_y1: float
    bbox_x2: float
    bbox_y2: float


class ObjectDetector:
    """
    Async YOLO11n wrapper that serializes inference through a single-worker
    ThreadPoolExecutor.

    The YOLO model is not thread-safe; max_workers=1 ensures all predict()
    calls are serialized and the model state is never accessed concurrently.

    Usage:
        detector = ObjectDetector(model_path="yolo11n.pt")
        detections = await detector.detect(frame_bytes)
        detector.shutdown()
    """

    def __init__(self, model_path: str = "yolo11n.pt") -> None:
        """
        Load the YOLO model and create the thread pool.

        Args:
            model_path: Path to model weights file. Use "yolo11n.pt" for
                        development (PyTorch) or "yolo11n_ncnn_model" for
                        Raspberry Pi 4 production (NCNN, 3-5x faster on ARM).
        """
        logger.info("Loading YOLO model from: %s", model_path)
        self.model = YOLO(model_path)
        # Single-worker executor serializes all inference calls.
        # YOLO is not thread-safe; never increase max_workers.
        self._executor = ThreadPoolExecutor(max_workers=1)
        logger.info("ObjectDetector initialized with model: %s", model_path)

    async def detect(self, frame_bytes: bytes) -> list[Detection]:
        """
        Run YOLO inference on a frame without blocking the asyncio event loop.

        Dispatches _predict_sync to the ThreadPoolExecutor via run_in_executor.
        Logs inference time and warns if it exceeds 300ms (thermal throttling
        indicator on Raspberry Pi 4).

        Args:
            frame_bytes: Raw image bytes (any PIL-supported format).

        Returns:
            List of Detection objects for all relevant classes found above
            CONFIDENCE_THRESHOLD. Empty list if no relevant detections or
            if frame decoding fails.
        """
        loop = asyncio.get_running_loop()
        t_start = time.monotonic()

        detections = await loop.run_in_executor(
            self._executor, self._predict_sync, frame_bytes
        )

        elapsed_ms = (time.monotonic() - t_start) * 1000
        logger.info(
            "YOLO inference completed in %.1fms — %d relevant detection(s)",
            elapsed_ms,
            len(detections),
        )

        if elapsed_ms > 300:
            logger.warning(
                "YOLO inference took %.1fms (>300ms threshold). "
                "Possible thermal throttling on Raspberry Pi 4. "
                "Check CPU temperature and active cooling.",
                elapsed_ms,
            )

        return detections

    def _predict_sync(self, frame_bytes: bytes) -> list[Detection]:
        """
        Synchronous YOLO inference — runs in ThreadPoolExecutor worker thread.

        Decodes frame_bytes via PIL, converts to numpy array, runs
        model.predict(), filters by RELEVANT_CLASSES and CONFIDENCE_THRESHOLD,
        and returns Detection objects.

        Args:
            frame_bytes: Raw image bytes (any PIL-supported format).

        Returns:
            List of Detection objects for relevant classes above threshold.
            Returns empty list on decoding failure (logged as exception).
        """
        try:
            img = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
            img_array = np.array(img)
        except Exception:
            logger.exception("Failed to decode frame bytes for YOLO inference")
            return []

        results = self.model.predict(img_array, verbose=False)
        detections: list[Detection] = []

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])

                if cls_id not in RELEVANT_CLASSES:
                    continue
                if confidence < CONFIDENCE_THRESHOLD:
                    continue

                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(
                    Detection(
                        label=RELEVANT_CLASSES[cls_id],
                        confidence=round(confidence, 4),
                        bbox_x1=round(x1, 2),
                        bbox_y1=round(y1, 2),
                        bbox_x2=round(x2, 2),
                        bbox_y2=round(y2, 2),
                    )
                )

        return detections

    def shutdown(self) -> None:
        """
        Shut down the ThreadPoolExecutor, waiting for any in-flight inference
        to complete before returning.
        """
        self._executor.shutdown(wait=True)
        logger.info("ObjectDetector ThreadPoolExecutor shut down")


def crop_person_bbox(frame_bytes: bytes, detection: Detection) -> bytes | None:
    """
    Crop the person bounding box from a frame and return it as JPEG bytes.

    Used by Phase 3 pipeline to extract a person region for FaceRecognizer.identify()
    instead of passing the full frame. Passing a tight crop improves face recognition
    accuracy when multiple people are present.

    Args:
        frame_bytes: Raw image bytes of the full frame.
        detection: Detection object for a "person" class detection.

    Returns:
        JPEG bytes of the cropped person region (quality=90), or None on any
        error (logged via logger.exception). None is also returned if the
        bounding box is degenerate (zero width or height after clamping).
    """
    try:
        img = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
        img_width, img_height = img.size

        # Clamp bounding box coordinates to image bounds
        x1 = max(0.0, detection.bbox_x1)
        y1 = max(0.0, detection.bbox_y1)
        x2 = min(float(img_width), detection.bbox_x2)
        y2 = min(float(img_height), detection.bbox_y2)

        # Validate non-degenerate box
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            logger.warning(
                "Degenerate bounding box after clamping: "
                "x1=%.1f y1=%.1f x2=%.1f y2=%.1f (image %dx%d)",
                x1, y1, x2, y2, img_width, img_height,
            )
            return None

        crop = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    except Exception:
        logger.exception(
            "Failed to crop person bbox (x1=%.1f y1=%.1f x2=%.1f y2=%.1f) from frame",
            detection.bbox_x1,
            detection.bbox_y1,
            detection.bbox_x2,
            detection.bbox_y2,
        )
        return None


def detections_to_dicts(detections: list[Detection]) -> list[dict]:
    """
    Convert a list of Detection objects to the dict format expected by
    EventStore.write_event(detections=...).

    This is the bridge between Phase 2 output (Detection dataclasses) and
    Phase 1 input (EventStore expects dicts with specific keys). Used in the
    Phase 3 pipeline after YOLO inference, before persisting an event.

    Args:
        detections: List of Detection objects from ObjectDetector.detect().

    Returns:
        List of dicts, each with keys: label, confidence, bbox_x1, bbox_y1,
        bbox_x2, bbox_y2. Compatible with EventStore.write_event(detections=...).
    """
    return [
        {
            "label": d.label,
            "confidence": d.confidence,
            "bbox_x1": d.bbox_x1,
            "bbox_y1": d.bbox_y1,
            "bbox_x2": d.bbox_x2,
            "bbox_y2": d.bbox_y2,
        }
        for d in detections
    ]

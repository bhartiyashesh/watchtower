# Phase 2: Object Detection - Research

**Researched:** 2026-02-25
**Domain:** YOLO11n object detection — async executor pattern, NCNN export, face recognition crop coordination
**Confidence:** HIGH (core patterns verified against official Ultralytics docs, Python asyncio official docs, and project architecture research)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| YOLO-01 | System runs YOLO11n object detection on every captured doorbell frame | `ObjectDetector` class wrapping `YOLO("yolo11n.pt")` or `YOLO("yolo11n_ncnn_model")`; called for every `frame_bytes` returned by Ring |
| YOLO-02 | System detects and labels: people, dogs, cats, cars, and packages | YOLO11n trained on COCO 80 classes — `person` (0), `car` (2), `cat` (15), `dog` (16) are native COCO classes; `package` requires COCO class `suitcase` (28) or a custom label map overlay |
| YOLO-03 | YOLO inference runs in a thread executor so it does not block the async event loop | `loop.run_in_executor(self._executor, self._predict_sync, frame_bytes)` with `ThreadPoolExecutor(max_workers=1)` |
| YOLO-04 | System stores detection class and confidence score per detected object in the event record | `detections` list of dicts `{label, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2}` passed to `EventStore.write_event()` — schema already exists from Phase 1 |
| YOLO-05 | Face recognition runs on YOLO-cropped person bounding boxes instead of full frame | Extract person bbox from `result.boxes.xyxy`, crop frame with Pillow/numpy, pass crop bytes to `FaceRecognizer.identify()` |
</phase_requirements>

---

## Summary

Phase 2 delivers `object_detector.py` — a thin wrapper that hides YOLO11n's synchronous `model.predict()` behind an async interface using a dedicated `ThreadPoolExecutor`. The module is the only new file this phase produces. It is tested in isolation before any wiring into the pipeline (pipeline wiring happens in Phase 3).

The three technical problems this phase solves are: (1) keeping YOLO inference off the asyncio event loop so Ring polling timestamps do not increase during inference; (2) exporting the model to NCNN format so production inference on Raspberry Pi 4 stays within the 250ms target; (3) routing face recognition through YOLO-cropped person bounding boxes instead of the full frame, which eliminates the HOG full-frame scan and reduces false negatives at doorbell distances.

The architecture is already fully decided in STATE.md: `ThreadPoolExecutor(max_workers=1)` to serialize YOLO calls (model is not thread-safe across concurrent callers), NCNN format for production on ARM CPU (3-5x faster than PyTorch format), and bounding box crop passed to the existing `FaceRecognizer.identify()` as image bytes. The `EventStore` interface from Phase 1 already accepts the `detections` list format this module produces — no schema changes are needed.

One open question about "package" detection: COCO does not have a `package` class. The closest proxy is `suitcase` (class ID 28). This is a known limitation of COCO-pretrained models that the planner should document in YOLO-02 tasks.

**Primary recommendation:** Build `ObjectDetector` as a class with `__init__` that loads the model once, a private `_predict_sync()` synchronous method that runs `model.predict()` and parses results into dicts, and a public `async detect()` method that dispatches to the executor. Keep frame input as `bytes` (consistent with what `ring_client.capture_frame()` returns and what `FaceRecognizer.identify()` expects).

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| ultralytics | 8.4.16 | YOLO11n model loading, inference, NCNN export | Official Ultralytics package — single install gives access to YOLO11n; `model.export(format="ncnn")` built-in; NCNN format delivers best inference speed on ARM CPU |
| concurrent.futures | stdlib | `ThreadPoolExecutor` for executor dispatch | Ships with Python; the documented pattern for offloading sync CPU-bound work from asyncio without adding dependencies |
| asyncio | stdlib | `loop.run_in_executor()` for async interface | Ships with Python; the only correct way to run blocking sync code without stalling the event loop |
| Pillow | 10.0.0+ (already installed) | Bounding box crop from full frame for face recognition | Already in requirements.txt; `Image.crop(bbox_tuple)` is a single-call operation |
| numpy | (pulled by ultralytics) | Frame bytes → numpy array conversion for YOLO input | Already required by ultralytics and existing face_recognition pipeline |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| opencv-python | 4.8.0+ (already installed) | Alternative frame decode from JPEG bytes | If numpy/PIL decode proves unreliable for JPEG bytes from Ring; `cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)` |
| logging | stdlib | Inference time logging per detection call | Required for thermal throttling monitoring — log each inference duration so degradation surfaces in logs before it becomes a failure |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `ThreadPoolExecutor(max_workers=1)` | `ProcessPoolExecutor(max_workers=1)` | `ProcessPoolExecutor` bypasses the GIL and provides true CPU parallelism, but adds pickling overhead for frame bytes and model state, and requires `if __name__ == '__main__'` guards. For a single-threaded serialized inference loop on RPi 4 with one camera, the GIL contention is not the bottleneck — the NCNN model itself is single-threaded internally. `ThreadPoolExecutor(max_workers=1)` is sufficient and simpler. |
| YOLO11n (nano) | YOLOv8n | Both are in the same `ultralytics` package; YOLO11n has 22% fewer parameters than YOLOv8n with ~2% higher mAP. Switch is one string: `YOLO("yolo11n.pt")` vs `YOLO("yolov8n.pt")`. YOLO11n is the current preferred model. |
| NCNN export for production | ONNX export | ONNX is simpler to export but NCNN is specifically optimized for ARM CPU mobile/embedded targets and delivers the best inference speed on RPi 4. ONNX is acceptable for development but must not be used in production on RPi 4. |
| Full-frame YOLO input | Resized/cropped YOLO input | YOLO11n default input size is 640x640. Processing at 320x320 (`imgsz=320`) reduces inference time at the cost of small-object detection accuracy. For doorbell use cases where persons are typically the largest object in frame, 320x320 may be acceptable. Benchmark both on actual hardware. |

**Installation (Phase 2 adds only ultralytics — everything else is already installed):**
```bash
pip install ultralytics==8.4.16
```

**NCNN model export (run once after install, before RPi deployment):**
```bash
python -c "from ultralytics import YOLO; m = YOLO('yolo11n.pt'); m.export(format='ncnn')"
# Creates: yolo11n_ncnn_model/ directory with param + bin files
```

---

## Architecture Patterns

### Recommended Project Structure (Phase 2 additions)

```
smart-lock-system/
├── object_detector.py      # NEW: YOLO11n wrapper — the only new file this phase
├── yolo11n.pt              # DOWNLOADED: PyTorch weights (dev; pre-download, copy to RPi)
├── yolo11n_ncnn_model/     # EXPORTED: NCNN model directory (production RPi only)
│   ├── yolo11n.ncnn.param
│   └── yolo11n.ncnn.bin
│
├── event_store.py          # EXISTING (Phase 1): unchanged
├── face_recognizer.py      # EXISTING: unchanged (but called with YOLO crop, not full frame)
├── main.py                 # EXISTING: unchanged in Phase 2 (wired in Phase 3)
└── requirements.txt        # UPDATE: add ultralytics==8.4.16
```

Phase 2 touches only `object_detector.py` (new file) and `requirements.txt`. No changes to existing runtime code — the wiring of `ObjectDetector` into the pipeline happens in Phase 3.

### Pattern 1: ObjectDetector Class with Executor Dispatch

**What:** A class that loads the YOLO model once at `__init__`, holds a `ThreadPoolExecutor(max_workers=1)`, and exposes an `async detect()` method that dispatches the synchronous `model.predict()` call to the executor.

**When to use:** Always. Never call `model.predict()` directly from an `async def` without this executor wrapper.

**Why `max_workers=1`:** The YOLO predictor class internally uses a threading lock, but sharing one model instance across multiple concurrent worker threads is not explicitly guaranteed thread-safe by Ultralytics for concurrent calls. Using `max_workers=1` serializes all inference calls through a single thread, eliminating any risk of concurrent model state access. At one Ring camera producing events every 30-120 seconds, serialization is not a bottleneck.

```python
# object_detector.py
# Source: Python asyncio official docs https://docs.python.org/3/library/asyncio-eventloop.html
# Source: Ultralytics thread-safe inference guide https://docs.ultralytics.com/guides/yolo-thread-safe-inference/
import asyncio
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from ultralytics import YOLO

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    label: str
    confidence: float
    bbox_x1: float
    bbox_y1: float
    bbox_x2: float
    bbox_y2: float


# COCO class IDs relevant to this project
# Full 80-class list: https://docs.ultralytics.com/datasets/detect/coco/
RELEVANT_CLASSES = {
    0: "person",
    2: "car",
    15: "cat",
    16: "dog",
    28: "suitcase",   # closest COCO proxy for "package"
}

# Minimum confidence threshold — detections below this are dropped
CONFIDENCE_THRESHOLD = 0.40


class ObjectDetector:
    """YOLO11n wrapper with async interface via ThreadPoolExecutor."""

    def __init__(self, model_path: str = "yolo11n.pt") -> None:
        """
        Load YOLO model once. Use 'yolo11n_ncnn_model' on Raspberry Pi 4 production.

        Args:
            model_path: Path to .pt file (dev) or ncnn model directory (production).
        """
        logger.info("Loading YOLO model: %s", model_path)
        self.model = YOLO(model_path)
        self._executor = ThreadPoolExecutor(max_workers=1)
        logger.info("ObjectDetector ready")

    async def detect(self, frame_bytes: bytes) -> list[Detection]:
        """
        Run YOLO inference on frame bytes. Non-blocking — dispatches to executor.

        Args:
            frame_bytes: JPEG image bytes from ring_client.capture_frame().

        Returns:
            List of Detection objects (may be empty if no objects found).
        """
        loop = asyncio.get_running_loop()
        start = time.monotonic()
        detections = await loop.run_in_executor(
            self._executor,
            self._predict_sync,
            frame_bytes,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("YOLO inference: %.0f ms, %d detections", elapsed_ms, len(detections))
        if elapsed_ms > 300:
            logger.warning(
                "YOLO inference exceeded 300ms (%.0f ms) — possible thermal throttling", elapsed_ms
            )
        return detections

    def _predict_sync(self, frame_bytes: bytes) -> list[Detection]:
        """Synchronous YOLO prediction. Called from executor thread only."""
        try:
            image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
            img_array = np.array(image)
        except Exception:
            logger.exception("Failed to decode frame bytes for YOLO")
            return []

        results = self.model.predict(img_array, verbose=False)
        detections: list[Detection] = []

        for result in results:
            if result.boxes is None:
                continue
            boxes = result.boxes
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i])
                conf = float(boxes.conf[i])
                if conf < CONFIDENCE_THRESHOLD:
                    continue
                # Map class ID to project label; skip unrelevant classes
                label = RELEVANT_CLASSES.get(cls_id)
                if label is None:
                    continue
                x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                detections.append(
                    Detection(
                        label=label,
                        confidence=round(conf, 4),
                        bbox_x1=round(x1, 1),
                        bbox_y1=round(y1, 1),
                        bbox_x2=round(x2, 1),
                        bbox_y2=round(y2, 1),
                    )
                )

        return detections

    def shutdown(self) -> None:
        """Shut down the executor cleanly. Call during process shutdown."""
        self._executor.shutdown(wait=True)
```

### Pattern 2: YOLO Crop → Face Recognition Integration

**What:** After YOLO detects a `person`, extract the bounding box from the Detection object, crop that region from the original full frame using Pillow, encode the crop as JPEG bytes, then pass those bytes to `FaceRecognizer.identify()`.

**When to use:** Any time at least one `Detection` with `label == "person"` is returned by `ObjectDetector.detect()`. If no person detected, skip face recognition entirely.

**Why crop instead of full frame:** The existing `FaceRecognizer.identify()` runs `face_recognition.face_locations(image_array, model="hog")` which scans the entire image. A 640x480 full doorbell frame contains sky, street, bushes — the HOG scan wastes time on non-face regions. Passing only the person bounding box crop reduces the search area by 70-90% and improves face recognition accuracy at doorbell distances where faces are relatively small in the full frame.

**Success criterion for YOLO-05:** `face_recognizer.identify(crop_bytes)` receives an image whose dimensions match the YOLO bounding box dimensions, not the original full frame. Confirm in tests by checking that the input image dimensions to `identify()` are approximately `(bbox_x2 - bbox_x1) x (bbox_y2 - bbox_y1)`.

```python
# Crop pattern — used in Phase 3 pipeline; documented here for the planner
# Source: Pillow official docs https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.crop
import io
from PIL import Image

def crop_person_bbox(frame_bytes: bytes, detection: Detection) -> bytes:
    """
    Crop person bounding box from full frame. Returns JPEG bytes of the crop.

    Args:
        frame_bytes: Original full-frame JPEG bytes from Ring.
        detection: Detection object with bbox_x1/y1/x2/y2 coordinates.

    Returns:
        JPEG bytes of the cropped person region, suitable for FaceRecognizer.identify().
    """
    image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
    # Clamp to image bounds — YOLO coords can occasionally exceed image dimensions
    w, h = image.size
    box = (
        max(0, int(detection.bbox_x1)),
        max(0, int(detection.bbox_y1)),
        min(w, int(detection.bbox_x2)),
        min(h, int(detection.bbox_y2)),
    )
    crop = image.crop(box)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=90)
    return buf.getvalue()
```

### Pattern 3: NCNN Model Export and Load

**What:** Export once during setup, then load the NCNN model directory for all production inference. Development on macOS uses `.pt` format; production on RPi 4 must use NCNN.

**When to use:** NCNN export is a one-time setup step per deployment machine. After export, point `model_path` at the `yolo11n_ncnn_model/` directory.

```python
# Export (run once, not in application code)
# Source: Ultralytics NCNN export docs https://docs.ultralytics.com/integrations/ncnn/
from ultralytics import YOLO
model = YOLO("yolo11n.pt")
model.export(format="ncnn")
# Creates: ./yolo11n_ncnn_model/yolo11n.ncnn.param + yolo11n.ncnn.bin

# Production load
detector = ObjectDetector(model_path="yolo11n_ncnn_model")
# Dev load (macOS, PyTorch)
detector = ObjectDetector(model_path="yolo11n.pt")
```

**Config integration:** Add `YOLO_MODEL_PATH` to `config.py`:
```python
# config.py
YOLO_MODEL_PATH: str = os.getenv("YOLO_MODEL_PATH", "yolo11n.pt")
# .env on RPi 4:
# YOLO_MODEL_PATH=yolo11n_ncnn_model
```

### Pattern 4: Benchmark and Validate Inference Time

**What:** Log and validate inference time per call. The 250ms target for RPi 4 is a project requirement (phase success criterion 2). Validation confirms the model and format meet the target before Phase 3 wires inference into the live pipeline.

**When to use:** In the Phase 2 integration test, not in production code. Run 20 inference calls on a representative frame and assert `median(latencies) < 250`.

```python
# Integration test — run on actual Raspberry Pi 4 hardware
import statistics
import time

async def benchmark_inference(detector, frame_bytes, n=20):
    latencies = []
    for _ in range(n):
        start = time.monotonic()
        await detector.detect(frame_bytes)
        latencies.append((time.monotonic() - start) * 1000)
    median_ms = statistics.median(latencies)
    p95_ms = sorted(latencies)[int(n * 0.95)]
    print(f"Median: {median_ms:.0f}ms, P95: {p95_ms:.0f}ms")
    assert median_ms < 250, f"Inference too slow: {median_ms:.0f}ms > 250ms target"
    return median_ms
```

### Anti-Patterns to Avoid

- **Calling `model.predict()` directly inside an `async def` without an executor:** Blocks the event loop for the entire inference duration (100-600ms on RPi 4). FastAPI requests stall, Ring polling misses events. Always use `run_in_executor`.

- **Using `ThreadPoolExecutor(max_workers=2+)` with a shared model instance:** Ultralytics documentation explicitly recommends against sharing one model instance across concurrent threads. `max_workers=1` serializes all calls through one worker thread.

- **Using PyTorch `.pt` format on Raspberry Pi 4 in production:** Community benchmarks report 400-600ms per frame on RPi 4B in PyTorch format. The 250ms target requires NCNN format. Never deploy with `.pt` on RPi 4.

- **Passing the full frame to `FaceRecognizer.identify()` after YOLO:** Defeats the performance benefit of YOLO detection. Always pass the YOLO-cropped person bounding box. If multiple persons are detected, use the highest-confidence person crop first.

- **Treating `suitcase` class as a perfect `package` proxy:** COCO class 28 (`suitcase`) is the closest available label for delivery packages, but will produce false positives on luggage and false negatives on flat parcels. Document this limitation in YOLO-02 task output.

- **Running the NCNN export on macOS and copying the model directory to RPi:** NCNN models are architecture-specific. The export must be run on the target ARM platform (RPi 4) or at minimum cross-compiled. Export on the RPi 4 itself using `yolo export model=yolo11n.pt format=ncnn`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Object detection model | Custom CNN or OpenCV-based detector | `ultralytics` YOLO11n | YOLO11n is pretrained on COCO with 2.6M parameters and 39.5 mAP; hand-rolled detectors for 5 class types at this scale would require weeks of data collection and training |
| Thread safety for inference | Custom mutex/lock around model calls | `ThreadPoolExecutor(max_workers=1)` | Single-worker executor naturally serializes calls without any application-level locking code; the executor manages the worker lifecycle |
| Frame bytes decode | Custom JPEG/PNG parser | `PIL.Image.open(io.BytesIO(frame_bytes))` | Pillow handles all JPEG variants including YCbCr, progressive JPEG, and EXIF rotation correctly; already installed |
| Bounding box coordinate clamping | Manual coordinate math | `min(w, max(0, coord))` with PIL `image.size` | One-liner per coordinate; use PIL `image.size` as the bounds reference |
| Inference timing | Custom timer instrumentation | `time.monotonic()` before/after `run_in_executor()` | Standard library monotonic clock is the correct tool; measures wall-clock elapsed time across executor thread |

**Key insight:** The executor pattern is the only piece of custom code required. Everything else (model loading, inference, result parsing, frame decoding, bbox cropping) is a direct call to Pillow or ultralytics APIs with documented return types.

---

## Common Pitfalls

### Pitfall 1: YOLO Inference Blocks the Async Event Loop

**What goes wrong:** `model.predict(frame)` called directly inside an `async def` stalls the event loop for 100-600ms. Ring polling timestamps jump; FastAPI requests queue up; Telegram alerts fire late.

**Why it happens:** `model.predict()` is synchronous and CPU-bound. Python asyncio is single-threaded — any synchronous code inside a coroutine blocks the loop completely.

**How to avoid:** Always use `loop.run_in_executor(self._executor, self._predict_sync, frame_bytes)`. The `_predict_sync` method runs in the executor thread while the event loop continues processing other coroutines.

**Warning signs:** Ring poll timestamps in logs show irregular gaps (e.g., normal 5s intervals suddenly becoming 5.5s or 6s) during inference.

**Verification:** Log `time.time()` before and after each `ring.wait_for_event()` call in the existing main loop. During YOLO inference, the interval should not increase if the executor is working correctly.

### Pitfall 2: PyTorch Format Too Slow on Raspberry Pi 4

**What goes wrong:** Developer tests YOLO on development Mac with `.pt` format (fast on Apple Silicon), deploys to RPi 4 without NCNN export, sees 400-600ms inference times that violate the 250ms target.

**Why it happens:** PyTorch format is not optimized for ARM CPU. NCNN is specifically built for mobile/embedded ARM targets and provides 2-4x speedup over PyTorch format on RPi 4B.

**How to avoid:** The NCNN export step must happen on or for the RPi 4 hardware. `config.py` must make `YOLO_MODEL_PATH` an env var so the production `.env` points at `yolo11n_ncnn_model` and the dev `.env` points at `yolo11n.pt`.

**Warning signs:** Inference latency logs consistently showing >400ms; `YOLO_MODEL_PATH` pointing at `.pt` file in production config.

### Pitfall 3: "Package" Class Not in COCO — Silent Misdetection

**What goes wrong:** YOLO-02 requires detecting "packages." COCO 80 classes does not include `package` or `parcel`. The model will never output a detection with `label="package"` because no such class exists in its training data. This means YOLO-02 is silently partially incomplete without explicit documentation.

**Why it happens:** COCO was designed for everyday objects, not delivery logistics. The closest class is `suitcase` (class ID 28) which covers rigid rectangular luggage-like objects.

**How to avoid:** Map COCO class 28 (`suitcase`) to the label `"package"` in the `RELEVANT_CLASSES` dict. Document this in code with a comment. Test with an actual package image to confirm the model detects it as `suitcase`/`package`. Accept the limitation: flat envelopes or padded mailers may not be detected.

**Warning signs:** Integration test with a package image produces zero detections (class not in `RELEVANT_CLASSES`).

### Pitfall 4: Face Recognition Runs on Full Frame Instead of YOLO Crop

**What goes wrong:** YOLO-05 says face recognition must run on the YOLO-cropped person bounding box. If the existing `FaceRecognizer.identify(frame_bytes)` is called with the full doorbell frame, the HOG face scan covers the entire image (sky, street, walls) — slower and more likely to miss a small face at doorbell distances.

**Why it happens:** It is simpler to pass the full `frame_bytes` to `identify()` without adding the crop step. The integration appears to work (face recognition still runs) but violates YOLO-05.

**How to avoid:** Implement `crop_person_bbox(frame_bytes, detection)` in `object_detector.py` and call it before `recognizer.identify()`. Verify in the test that the input image dimensions to `identify()` approximately match the bounding box dimensions (not the full frame dimensions).

**Warning signs:** `face_recognition.face_locations()` log messages showing image size equal to the full frame size (e.g., 640x480) rather than a smaller person crop.

### Pitfall 5: NCNN Export Must Run on ARM, Not macOS

**What goes wrong:** Developer exports YOLO to NCNN on macOS Apple Silicon and copies the `yolo11n_ncnn_model/` directory to the RPi 4. The NCNN model files may be architecture-specific or produce incorrect results on the ARM target.

**Why it happens:** NCNN param/bin files are generally portable, but the export process may bake in platform-specific optimizations. The safe pattern is to run the export on the target device.

**How to avoid:** Run `model.export(format="ncnn")` directly on the Raspberry Pi 4. The `.pt` model weights file can be copied from macOS (it is platform-independent); the export step must run on the RPi 4 with `ultralytics` installed.

**Warning signs:** Model loads on RPi but produces zero detections or random wrong class outputs.

### Pitfall 6: No `if __name__ == '__main__'` Guard Needed for ThreadPoolExecutor (But Required for ProcessPoolExecutor)

**What goes wrong:** If the decision to use `ProcessPoolExecutor` is made later (e.g., for RPi 5), the worker spawning requires `if __name__ == '__main__'` in the entry point. Missing this causes `RuntimeError: An attempt has been made to start a new process before the current process has finished its bootstrapping phase` on multiprocessing spawn.

**Why it happens:** `multiprocessing` module's "spawn" start method (default on Windows and macOS) re-imports the main module for each worker. Without the guard, the import triggers another worker spawn, and so on recursively.

**How to avoid:** `ThreadPoolExecutor(max_workers=1)` — the chosen approach for Phase 2 — does NOT require this guard. Document this explicitly so the pattern is not changed without understanding the spawn requirement.

---

## Code Examples

Verified patterns from official sources:

### Complete ObjectDetector Implementation Skeleton

```python
# object_detector.py
# Sources:
#   Python asyncio run_in_executor: https://docs.python.org/3/library/asyncio-eventloop.html
#   Ultralytics predict API: https://docs.ultralytics.com/usage/python/
#   Ultralytics thread-safe inference: https://docs.ultralytics.com/guides/yolo-thread-safe-inference/
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

RELEVANT_CLASSES = {
    0: "person",
    2: "car",
    15: "cat",
    16: "dog",
    28: "package",  # COCO "suitcase" — best available proxy for delivery packages
}
CONFIDENCE_THRESHOLD = 0.40


@dataclass
class Detection:
    label: str
    confidence: float
    bbox_x1: float
    bbox_y1: float
    bbox_x2: float
    bbox_y2: float


class ObjectDetector:
    def __init__(self, model_path: str = "yolo11n.pt") -> None:
        self.model = YOLO(model_path)
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def detect(self, frame_bytes: bytes) -> list[Detection]:
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        result = await loop.run_in_executor(self._executor, self._predict_sync, frame_bytes)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("YOLO inference %.0fms, %d detections", elapsed_ms, len(result))
        if elapsed_ms > 300:
            logger.warning("Inference %.0fms exceeds 300ms — possible thermal throttling", elapsed_ms)
        return result

    def _predict_sync(self, frame_bytes: bytes) -> list[Detection]:
        try:
            img = np.array(Image.open(io.BytesIO(frame_bytes)).convert("RGB"))
        except Exception:
            logger.exception("Frame decode failed")
            return []

        results = self.model.predict(img, verbose=False)
        detections: list[Detection] = []
        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                cls_id = int(r.boxes.cls[i])
                conf = float(r.boxes.conf[i])
                label = RELEVANT_CLASSES.get(cls_id)
                if label is None or conf < CONFIDENCE_THRESHOLD:
                    continue
                x1, y1, x2, y2 = r.boxes.xyxy[i].tolist()
                detections.append(Detection(label, round(conf, 4),
                                            round(x1, 1), round(y1, 1),
                                            round(x2, 1), round(y2, 1)))
        return detections

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
```

### Person Bounding Box Crop for Face Recognition

```python
# Source: Pillow Image.crop docs https://pillow.readthedocs.io/en/stable/reference/Image.html
def crop_person_bbox(frame_bytes: bytes, detection: Detection) -> bytes | None:
    """Crop person bbox from full frame. Returns JPEG bytes for FaceRecognizer.identify()."""
    try:
        image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
        w, h = image.size
        box = (
            max(0, int(detection.bbox_x1)),
            max(0, int(detection.bbox_y1)),
            min(w, int(detection.bbox_x2)),
            min(h, int(detection.bbox_y2)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None  # degenerate box
        crop = image.crop(box)
        buf = io.BytesIO()
        crop.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        logger.exception("Person crop failed")
        return None
```

### Detecting Event Loop Blocking (Verification Pattern)

```python
# Integration test: confirm Ring poll interval does not increase during YOLO inference
# Run as a standalone async test, not in pytest (requires actual Ring connection)
import asyncio
import time

async def verify_non_blocking(detector: ObjectDetector, frame_bytes: bytes):
    intervals = []
    last_t = time.monotonic()

    async def heartbeat():
        nonlocal last_t
        for _ in range(30):
            await asyncio.sleep(0.5)
            now = time.monotonic()
            intervals.append(now - last_t)
            last_t = now

    async def inference_loop():
        for _ in range(5):
            await detector.detect(frame_bytes)
            await asyncio.sleep(0.1)

    await asyncio.gather(heartbeat(), inference_loop())
    max_gap = max(intervals)
    print(f"Max heartbeat gap: {max_gap*1000:.0f}ms (must stay near 500ms)")
    assert max_gap < 0.8, f"Event loop blocked: max gap {max_gap*1000:.0f}ms"
```

### NCNN Export Command (Run Once on RPi 4)

```python
# Run from the RPi 4 command line after `pip install ultralytics`:
# python -c "from ultralytics import YOLO; YOLO('yolo11n.pt').export(format='ncnn')"
#
# Loads:  yolo11n_ncnn_model/
#         ├── yolo11n.ncnn.param
#         └── yolo11n.ncnn.bin
#
# Then update .env:
# YOLO_MODEL_PATH=yolo11n_ncnn_model
```

### Detections to EventStore Format Conversion

```python
# Convert Detection objects to the dict format EventStore.write_event() expects
# Source: event_store.py (Phase 1 output) — write_event() detections parameter
def detections_to_dicts(detections: list[Detection]) -> list[dict]:
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
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| YOLOv8n for nano detection | YOLO11n (22% fewer params, ~2% higher mAP) | Ultralytics YOLO11 release, Sept 2024 | Better accuracy at same or lower compute cost; same `ultralytics` package, one-string switch |
| PyTorch `.pt` for all platforms | NCNN for ARM, `.pt` for dev | Ultralytics NCNN integration doc (2024) | 2-4x inference speedup on ARM CPU; required for <250ms on RPi 4 |
| Face recognition on full frame | Face recognition on YOLO person crop | Standard pattern since YOLO went mainstream (2018+) | 70-90% reduction in HOG scan area; higher accuracy at doorbell distances where faces are small in full frame |
| `asyncio.to_thread()` for blocking calls | `loop.run_in_executor(ThreadPoolExecutor)` | Both available since Python 3.9; explicit executor gives `max_workers` control | Explicit `max_workers=1` serializes YOLO calls without extra locking code |
| YOLO model instance per thread | Shared single instance with `max_workers=1` executor | Ultralytics thread-safe inference guide (2024) | Memory-efficient (one model in RAM) while maintaining thread safety through call serialization |

**Deprecated/outdated:**
- `model.predict()` called directly inside `async def`: Still technically runs, but blocks the event loop. Not acceptable in this architecture.
- `nest_asyncio` for running blocking code in existing loops: A Jupyter notebook shim, not production code. Never use in this project.

---

## Open Questions

1. **Actual YOLO11n NCNN inference time on this specific RPi 4 board**
   - What we know: Official Ultralytics RPi docs report NCNN delivers best inference performance; community reports 400-600ms for PyTorch format on RPi 4B; NCNN typically gives 2-4x speedup; RPi 5 achieves ~8 FPS (~125ms/frame) with YOLO11n NCNN
   - What's unclear: Exact ms latency on the project's specific RPi 4 board revision with its cooling configuration. RPi 4 is significantly slower than RPi 5. The 250ms target may be tight.
   - Recommendation: Benchmark on actual hardware early in Phase 2 implementation. If median inference exceeds 250ms even with NCNN, reduce input resolution from 640 to 320: `model.predict(img, imgsz=320, verbose=False)`. This halves input area and typically reduces inference time by 40-60% at the cost of small-object detection accuracy.

2. **"Package" detection accuracy with COCO `suitcase` class**
   - What we know: COCO has no `package` class; class 28 (`suitcase`) is the closest proxy for rectangular box-like objects
   - What's unclear: How accurately the `suitcase` class fires on delivery boxes and mailers in real doorbell footage
   - Recommendation: Test with 5-10 actual doorbell frame images containing delivery boxes. If detection rate is unacceptably low, document as a known limitation and consider the `backpack` class (24) as a secondary proxy for soft packages.

3. **Multiple persons in frame — which crop to use for face recognition**
   - What we know: YOLO can return multiple `person` detections per frame
   - What's unclear: Should the system try all persons or only the highest-confidence one?
   - Recommendation: Use the person with the highest YOLO confidence score as the primary face recognition target. If no match, try remaining person detections in descending confidence order. This adds complexity to Phase 3 but is mentioned here so the planner can account for it.

---

## Sources

### Primary (HIGH confidence)

- [Ultralytics YOLO Thread-Safe Inference Guide](https://docs.ultralytics.com/guides/yolo-thread-safe-inference/) — `max_workers=1` rationale, ThreadingLocked decorator
- [Ultralytics YOLO model.predict() API](https://docs.ultralytics.com/usage/python/) — results object fields: `boxes.xyxy`, `boxes.conf`, `boxes.cls`
- [Ultralytics NCNN Export Docs](https://docs.ultralytics.com/integrations/ncnn/) — export command, post-export load pattern
- [Ultralytics Raspberry Pi Guide](https://docs.ultralytics.com/guides/raspberry-pi/) — NCNN format recommendation, ARM optimization rationale
- [Ultralytics YOLO11 Model Page](https://docs.ultralytics.com/models/yolo11/) — 2.6M parameters, 39.5 mAP, 56.1ms CPU ONNX baseline
- [Python asyncio run_in_executor](https://docs.python.org/3/library/asyncio-eventloop.html) — `loop.run_in_executor(executor, func, *args)` signature, ThreadPoolExecutor vs ProcessPoolExecutor guidance
- [Pillow Image.crop](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.crop) — bounding box crop pattern
- `.planning/STATE.md` — locked decisions: NCNN format, `ThreadPoolExecutor(max_workers=1)`, face recognition on YOLO crop
- `event_store.py` (Phase 1 output) — `detections` dict format accepted by `EventStore.write_event()`
- `face_recognizer.py` (existing) — `FaceRecognizer.identify(image_bytes: bytes)` call signature; confirms bytes input

### Secondary (MEDIUM confidence)

- [Ultralytics GitHub Issue #12996](https://github.com/ultralytics/ultralytics/issues/12996) — community benchmarks showing 400-600ms PyTorch format on RPi 4B
- [Qengineering YoloV8-ncnn-Raspberry-Pi-4](https://github.com/Qengineering/YoloV8-ncnn-Raspberry-Pi-4) — RPi 4 NCNN implementation reference
- [Ultralytics community: informal benchmark results](https://community.ultralytics.com/t/unofficial-benchmark-results-how-fast-can-you-yolo/59) — community inference timing data
- [LearnOpenCV: YOLO11 on Raspberry Pi](https://learnopencv.com/yolo11-on-raspberry-pi/) — RPi 5 achieves ~8 FPS NCNN, directional guidance for RPi 4

### Tertiary (LOW confidence)

- [YOLO11 arxiv technical paper](https://arxiv.org/html/2510.09653v2) — architectural overview; not needed for implementation
- [Face Detection + YOLOv8 reference pipeline](https://github.com/erfan-mtzv/Face-Detection-and-Recognition-with-YOLOv8-and-FaceNet-PyTorch) — reference only; uses FaceNet not dlib

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — `ultralytics` package, executor pattern, NCNN export all verified against official Ultralytics and Python docs
- Architecture patterns: HIGH — executor dispatch pattern is directly from Python asyncio official docs; NCNN export pattern from official Ultralytics docs; crop pattern from official Pillow docs
- Pitfalls: HIGH for event loop blocking and PyTorch format speed (multiple sources including official docs and community reports); MEDIUM for "package" class limitation (COCO class list is authoritative, effectiveness of `suitcase` proxy is empirical); MEDIUM for NCNN RPi 4 latency (RPi 5 benchmarks available, RPi 4 specific numbers require actual hardware measurement)

**Research date:** 2026-02-25
**Valid until:** 2026-08-25 (stable — ultralytics NCNN API, Python asyncio executor API, and Pillow crop API are stable; YOLO11n is current model as of research date)

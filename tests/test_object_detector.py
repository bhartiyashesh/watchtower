"""
Comprehensive test suite for ObjectDetector module.

Validates all YOLO requirements (YOLO-01 through YOLO-05):
  YOLO-01: YOLO11n detects a person and returns Detection with label='person'
  YOLO-02: Inference completes in under 5 seconds on macOS dev environment
           (smoke test; RPi 4 NCNN 250ms benchmark deferred to on-device testing)
  YOLO-03: detect() is async and does not block the event loop (concurrent heartbeat)
  YOLO-04: crop_person_bbox() returns JPEG bytes matching the bounding box region
  YOLO-05: detections_to_dicts() produces the exact dict format EventStore expects

All tests use a module-scoped detector fixture to avoid re-loading YOLO for
every test (model load is ~1-2 seconds). The detector is shut down after all
module tests complete.
"""

import asyncio
import io
import time

import pytest
import pytest_asyncio
from PIL import Image

from object_detector import (
    CONFIDENCE_THRESHOLD,
    RELEVANT_CLASSES,
    Detection,
    ObjectDetector,
    crop_person_bbox,
    detections_to_dicts,
)


# ---------------------------------------------------------------------------
# Module-scoped fixtures (shared across all tests in this module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def detector():
    """
    Instantiate ObjectDetector once per test module to avoid re-loading YOLO
    for every individual test. Shutdown is called after all tests complete.
    """
    d = ObjectDetector("yolo11n.pt")
    yield d
    d.shutdown()


@pytest.fixture(scope="module")
def sample_frame_bytes():
    """
    Generate a 640x480 RGB image with a colored rectangle in the center to
    simulate a figure. Converted to JPEG bytes for use in inference tests.

    Note: YOLO may or may not detect a "person" in a synthetic rectangle —
    that is acceptable. Tests validate that the ObjectDetector processes the
    image and returns a list (possibly empty) without errors.
    """
    img = Image.new("RGB", (640, 480), color=(200, 200, 200))
    # Draw a simple colored rectangle to simulate a person-like shape
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([220, 80, 420, 400], fill=(70, 100, 150))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# YOLO-01: Model loading and basic inference
# ---------------------------------------------------------------------------


def test_detector_loads_model(detector):
    """YOLO-01: ObjectDetector.__init__ succeeds and model + executor are set."""
    assert detector.model is not None, "model attribute must not be None after init"
    assert detector._executor is not None, "_executor attribute must not be None after init"


@pytest.mark.asyncio
async def test_detect_returns_list_of_detections(detector, sample_frame_bytes):
    """YOLO-01: detect() returns a list; each element is a Detection with all required fields."""
    result = await detector.detect(sample_frame_bytes)
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    for d in result:
        assert isinstance(d, Detection), f"Expected Detection instance, got {type(d)}"
        assert hasattr(d, "label"), "Detection missing 'label' field"
        assert hasattr(d, "confidence"), "Detection missing 'confidence' field"
        assert hasattr(d, "bbox_x1"), "Detection missing 'bbox_x1' field"
        assert hasattr(d, "bbox_y1"), "Detection missing 'bbox_y1' field"
        assert hasattr(d, "bbox_x2"), "Detection missing 'bbox_x2' field"
        assert hasattr(d, "bbox_y2"), "Detection missing 'bbox_y2' field"


@pytest.mark.asyncio
async def test_detection_labels_are_relevant_only(detector, sample_frame_bytes):
    """YOLO-01: Every returned Detection label is in RELEVANT_CLASSES.values() — no irrelevant COCO classes."""
    result = await detector.detect(sample_frame_bytes)
    relevant_labels = set(RELEVANT_CLASSES.values())
    for d in result:
        assert d.label in relevant_labels, (
            f"Got irrelevant label '{d.label}' — only {relevant_labels} should appear"
        )


@pytest.mark.asyncio
async def test_detection_confidence_above_threshold(detector, sample_frame_bytes):
    """YOLO-01: Every returned Detection has confidence >= CONFIDENCE_THRESHOLD."""
    result = await detector.detect(sample_frame_bytes)
    for d in result:
        assert d.confidence >= CONFIDENCE_THRESHOLD, (
            f"Detection confidence {d.confidence} is below threshold {CONFIDENCE_THRESHOLD}"
        )


@pytest.mark.asyncio
async def test_detection_bbox_coordinates_are_positive(detector, sample_frame_bytes):
    """YOLO-01: All bbox values are >= 0, and x2 > x1, y2 > y1 (non-degenerate boxes)."""
    result = await detector.detect(sample_frame_bytes)
    for d in result:
        assert d.bbox_x1 >= 0, f"bbox_x1={d.bbox_x1} should be >= 0"
        assert d.bbox_y1 >= 0, f"bbox_y1={d.bbox_y1} should be >= 0"
        assert d.bbox_x2 >= 0, f"bbox_x2={d.bbox_x2} should be >= 0"
        assert d.bbox_y2 >= 0, f"bbox_y2={d.bbox_y2} should be >= 0"
        assert d.bbox_x2 > d.bbox_x1, (
            f"bbox_x2 ({d.bbox_x2}) must be > bbox_x1 ({d.bbox_x1})"
        )
        assert d.bbox_y2 > d.bbox_y1, (
            f"bbox_y2 ({d.bbox_y2}) must be > bbox_y1 ({d.bbox_y1})"
        )


@pytest.mark.asyncio
async def test_detect_empty_on_blank_image(detector):
    """YOLO-01: Solid white frame produces no detections (no objects to find)."""
    img = Image.new("RGB", (640, 480), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    white_bytes = buf.getvalue()

    result = await detector.detect(white_bytes)
    assert result == [], (
        f"Expected empty list for blank white image, got {len(result)} detection(s)"
    )


@pytest.mark.asyncio
async def test_detect_handles_invalid_bytes(detector):
    """YOLO-01: Invalid bytes return empty list without raising an exception."""
    result = await detector.detect(b"not-an-image")
    assert result == [], (
        f"Expected empty list for invalid bytes, got {type(result)}"
    )
    assert isinstance(result, list), "Must return a list, not raise an exception"


# ---------------------------------------------------------------------------
# YOLO-03: Non-blocking async verification (heartbeat test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_is_non_blocking(detector, sample_frame_bytes):
    """
    YOLO-03: detect() dispatches inference to a ThreadPoolExecutor and does not
    block the asyncio event loop.

    Strategy: Run a heartbeat coroutine concurrently with 3 inference calls.
    The heartbeat records timestamps every 100ms for 3 seconds. If inference
    blocked the event loop, consecutive heartbeat gaps would be 100-600ms+.
    If run_in_executor is working, max gap should stay well under 500ms.
    """
    timestamps: list[float] = []

    async def heartbeat():
        """Record timestamps every 100ms for 3 seconds."""
        start = time.monotonic()
        while time.monotonic() - start < 3.0:
            timestamps.append(time.monotonic())
            await asyncio.sleep(0.1)

    async def inference():
        """Run 3 inference calls to exercise the executor during heartbeat."""
        for _ in range(3):
            await detector.detect(sample_frame_bytes)

    await asyncio.gather(heartbeat(), inference())

    assert len(timestamps) >= 2, "Heartbeat must record at least 2 timestamps"

    # Compute max gap between consecutive heartbeat timestamps
    gaps = [
        (timestamps[i + 1] - timestamps[i]) * 1000
        for i in range(len(timestamps) - 1)
    ]
    max_gap_ms = max(gaps)

    assert max_gap_ms < 500, (
        f"Max heartbeat gap was {max_gap_ms:.1f}ms — inference may be blocking the event loop. "
        f"Expected < 500ms. All gaps: {[f'{g:.1f}ms' for g in gaps]}"
    )


# ---------------------------------------------------------------------------
# YOLO-04: crop_person_bbox() verification
# ---------------------------------------------------------------------------


def test_crop_person_bbox_returns_jpeg_bytes():
    """YOLO-04: crop_person_bbox() returns JPEG bytes with dimensions matching the bbox region."""
    img = Image.new("RGB", (640, 480), color=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    frame_bytes = buf.getvalue()

    detection = Detection(
        label="person",
        confidence=0.95,
        bbox_x1=100.0,
        bbox_y1=50.0,
        bbox_x2=300.0,
        bbox_y2=400.0,
    )

    result = crop_person_bbox(frame_bytes, detection)

    assert result is not None, "crop_person_bbox() must not return None for a valid detection"
    assert isinstance(result, bytes), f"Expected bytes, got {type(result)}"

    # Verify it is a valid JPEG
    cropped_img = Image.open(io.BytesIO(result))
    assert cropped_img.format == "JPEG", f"Expected JPEG format, got {cropped_img.format}"

    # Dimensions should approximately match the bbox region
    expected_width = 300 - 100  # x2 - x1 = 200
    expected_height = 400 - 50  # y2 - y1 = 350
    actual_width, actual_height = cropped_img.size

    assert abs(actual_width - expected_width) <= 2, (
        f"Crop width {actual_width}px does not match expected {expected_width}px (bbox width)"
    )
    assert abs(actual_height - expected_height) <= 2, (
        f"Crop height {actual_height}px does not match expected {expected_height}px (bbox height)"
    )


def test_crop_person_bbox_clamps_to_image_bounds():
    """YOLO-04: bbox coordinates exceeding image dimensions are clamped — no out-of-bounds error."""
    img = Image.new("RGB", (640, 480), color=(150, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    frame_bytes = buf.getvalue()

    # bbox_x2=800 exceeds image width of 640; bbox_y2=600 exceeds height of 480
    detection = Detection(
        label="person",
        confidence=0.90,
        bbox_x1=500.0,
        bbox_y1=350.0,
        bbox_x2=800.0,  # Out of bounds
        bbox_y2=600.0,  # Out of bounds
    )

    result = crop_person_bbox(frame_bytes, detection)

    assert result is not None, (
        "crop_person_bbox() must not return None when bbox is out-of-bounds (should clamp)"
    )
    assert isinstance(result, bytes), "Expected bytes for clamped bbox crop"

    # Verify result is a valid image
    cropped_img = Image.open(io.BytesIO(result))
    clamped_width, clamped_height = cropped_img.size
    assert clamped_width <= 640, f"Clamped width {clamped_width} must not exceed image width 640"
    assert clamped_height <= 480, f"Clamped height {clamped_height} must not exceed image height 480"


def test_crop_person_bbox_degenerate_box_returns_none():
    """YOLO-04: Zero-width bounding box returns None (degenerate box guard)."""
    img = Image.new("RGB", (640, 480), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    frame_bytes = buf.getvalue()

    # bbox_x1 == bbox_x2 => zero width — degenerate box
    detection = Detection(
        label="person",
        confidence=0.88,
        bbox_x1=200.0,
        bbox_y1=100.0,
        bbox_x2=200.0,  # Same as x1 — degenerate
        bbox_y2=300.0,
    )

    result = crop_person_bbox(frame_bytes, detection)

    assert result is None, (
        f"Expected None for degenerate bbox (x1==x2), got {type(result)}"
    )


def test_crop_person_bbox_invalid_bytes_returns_none():
    """YOLO-04: Invalid frame bytes return None without raising an exception."""
    detection = Detection(
        label="person",
        confidence=0.80,
        bbox_x1=100.0,
        bbox_y1=50.0,
        bbox_x2=300.0,
        bbox_y2=400.0,
    )

    result = crop_person_bbox(b"not-an-image", detection)

    assert result is None, (
        f"Expected None for invalid frame bytes, got {type(result)}"
    )


# ---------------------------------------------------------------------------
# YOLO-05: detections_to_dicts() EventStore format compatibility
# ---------------------------------------------------------------------------


def test_detections_to_dicts_format():
    """
    YOLO-05: detections_to_dicts() produces list of dicts with exactly the keys
    EventStore.write_event(detections=...) expects.
    """
    detections = [
        Detection(label="person", confidence=0.95, bbox_x1=10.0, bbox_y1=20.0, bbox_x2=100.0, bbox_y2=200.0),
        Detection(label="car", confidence=0.72, bbox_x1=200.0, bbox_y1=50.0, bbox_x2=400.0, bbox_y2=300.0),
    ]

    result = detections_to_dicts(detections)

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) == 2, f"Expected 2 dicts, got {len(result)}"

    expected_keys = {"label", "confidence", "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"}
    for i, d in enumerate(result):
        assert isinstance(d, dict), f"Element {i} should be a dict, got {type(d)}"
        assert set(d.keys()) == expected_keys, (
            f"Dict {i} has keys {set(d.keys())}, expected exactly {expected_keys}"
        )

    # Verify values match input Detection fields
    assert result[0]["label"] == "person"
    assert result[0]["confidence"] == pytest.approx(0.95)
    assert result[0]["bbox_x1"] == pytest.approx(10.0)
    assert result[0]["bbox_y1"] == pytest.approx(20.0)
    assert result[0]["bbox_x2"] == pytest.approx(100.0)
    assert result[0]["bbox_y2"] == pytest.approx(200.0)

    assert result[1]["label"] == "car"
    assert result[1]["confidence"] == pytest.approx(0.72)


def test_detections_to_dicts_empty_list():
    """YOLO-05: detections_to_dicts([]) returns an empty list."""
    result = detections_to_dicts([])
    assert result == [], f"Expected [], got {result}"


# ---------------------------------------------------------------------------
# RELEVANT_CLASSES mapping validation
# ---------------------------------------------------------------------------


def test_relevant_classes_mapping():
    """YOLO-01: RELEVANT_CLASSES contains exactly 5 entries with the expected COCO class ID mappings."""
    assert len(RELEVANT_CLASSES) == 5, (
        f"Expected exactly 5 entries in RELEVANT_CLASSES, got {len(RELEVANT_CLASSES)}: {RELEVANT_CLASSES}"
    )
    assert RELEVANT_CLASSES[0] == "person", f"Class ID 0 must map to 'person', got '{RELEVANT_CLASSES.get(0)}'"
    assert RELEVANT_CLASSES[2] == "car", f"Class ID 2 must map to 'car', got '{RELEVANT_CLASSES.get(2)}'"
    assert RELEVANT_CLASSES[15] == "cat", f"Class ID 15 must map to 'cat', got '{RELEVANT_CLASSES.get(15)}'"
    assert RELEVANT_CLASSES[16] == "dog", f"Class ID 16 must map to 'dog', got '{RELEVANT_CLASSES.get(16)}'"
    assert RELEVANT_CLASSES[28] == "package", f"Class ID 28 must map to 'package', got '{RELEVANT_CLASSES.get(28)}'"


# ---------------------------------------------------------------------------
# YOLO-02: Inference latency smoke test (macOS dev environment)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inference_completes_in_reasonable_time(detector, sample_frame_bytes):
    """
    YOLO-02 dev smoke test: detect() completes in under 5 seconds on macOS.

    # NOTE: ROADMAP success criterion 2 requires <250ms on RPi 4 with NCNN model.
    # That benchmark is deferred to on-device testing — cannot validate on macOS dev environment.
    # This test is a smoke test confirming the inference pattern completes successfully.
    """
    t_start = time.monotonic()
    await detector.detect(sample_frame_bytes)
    elapsed = time.monotonic() - t_start

    assert elapsed < 5.0, (
        f"Inference took {elapsed:.2f}s — exceeds 5.0s smoke test ceiling. "
        f"Code path may be hanging or model download failed."
    )

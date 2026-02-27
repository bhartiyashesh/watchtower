import io
import logging
from pathlib import Path

import face_recognition
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class FaceRecognizer:
    """Loads known faces and matches against doorbell snapshots."""

    def __init__(self, known_faces_dir: Path, tolerance: float = 0.5):
        self.tolerance = tolerance
        self.known_faces_dir = known_faces_dir
        self.known_encodings: list[np.ndarray] = []
        self.known_names: list[str] = []
        self._load_known_faces(known_faces_dir)

    def reload(self) -> None:
        """Clear and re-load all known face encodings from disk."""
        self.known_encodings.clear()
        self.known_names.clear()
        self._load_known_faces(self.known_faces_dir)
        logger.info("Face encodings reloaded (%d encodings)", len(self.known_encodings))

    def enroll(self, name: str, image_bytes: bytes, extension: str = ".jpg") -> str:
        """Validate an image has exactly one face, save to known_faces, and reload.

        Args:
            name: Person identifier (e.g. 'yashesh').
            image_bytes: Raw image file bytes.
            extension: File extension including dot (e.g. '.jpg').

        Returns:
            The saved filename (e.g. 'yashesh_3.jpg').

        Raises:
            ValueError: If no face or multiple faces are detected.
        """
        image = face_recognition.load_image_file(io.BytesIO(image_bytes))
        locations = face_recognition.face_locations(image)

        if len(locations) == 0:
            raise ValueError("No face detected in the uploaded image")
        if len(locations) > 1:
            raise ValueError(f"Multiple faces ({len(locations)}) detected — upload a photo with exactly one face")

        # Determine next index
        existing = list(self.known_faces_dir.glob(f"{name}_*"))
        next_idx = len(existing) + 1
        filename = f"{name}_{next_idx}{extension.lower()}"
        dest = self.known_faces_dir / filename

        # Save the image
        img = Image.open(io.BytesIO(image_bytes))
        img.save(str(dest), "JPEG", quality=95)

        # Reload encodings
        self.reload()
        return filename

    def _load_known_faces(self, directory: Path) -> None:
        """Load and encode all face images from the known_faces directory.

        Directory structure:
            known_faces/
                yashesh_1.jpg
                yashesh_2.jpg
                family_member_1.jpg
        
        The name is derived from the filename (before the underscore/number).
        Multiple images per person improve accuracy.
        """
        extensions = {".jpg", ".jpeg", ".png", ".bmp"}

        for img_path in sorted(directory.iterdir()):
            if img_path.suffix.lower() not in extensions:
                continue

            name = img_path.stem.rsplit("_", 1)[0]

            try:
                image = face_recognition.load_image_file(str(img_path))
                encodings = face_recognition.face_encodings(image)

                if encodings:
                    self.known_encodings.append(encodings[0])
                    self.known_names.append(name)
                    logger.info(f"Loaded face: {name} from {img_path.name}")
                else:
                    logger.warning(f"No face found in {img_path.name}")
            except Exception as e:
                logger.error(f"Error loading {img_path.name}: {e}")

        logger.info(
            f"Loaded {len(self.known_encodings)} face encoding(s) "
            f"for {len(set(self.known_names))} person(s)"
        )

    def identify(self, image_bytes: bytes) -> str | None:
        """Identify a face from snapshot bytes. Returns the matched name or None."""
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image_array = np.array(image.convert("RGB"))
        except Exception as e:
            logger.error(f"Failed to decode image: {e}")
            return None

        # Save the frame for debugging
        debug_dir = Path("debug_frames")
        debug_dir.mkdir(exist_ok=True)
        import time as _time
        debug_path = debug_dir / f"frame_{int(_time.time())}.jpg"
        image.save(debug_path)
        logger.info(f"Saved debug frame to {debug_path}")

        face_locations = face_recognition.face_locations(image_array, model="hog")
        if not face_locations:
            logger.info("No faces detected in frame")
            return None

        logger.info(f"Detected {len(face_locations)} face(s) in frame")

        unknown_encodings = face_recognition.face_encodings(image_array, face_locations)

        for encoding in unknown_encodings:
            distances = face_recognition.face_distance(self.known_encodings, encoding)

            if len(distances) == 0:
                continue

            best_idx = np.argmin(distances)
            best_distance = round(distances[best_idx], 3)
            closest_name = self.known_names[best_idx]
            logger.info(f"Closest match: {closest_name} (distance: {best_distance}, "
                        f"tolerance: {self.tolerance})")

            if distances[best_idx] <= self.tolerance:
                confidence = round(1 - distances[best_idx], 3)
                logger.info(f"Face matched: {closest_name} (confidence: {confidence})")
                return closest_name

        logger.info("Face detected but distance too high — no match")
        return None

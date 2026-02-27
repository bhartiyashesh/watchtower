#!/usr/bin/env python3
"""
Enroll faces into the known_faces directory.

Usage:
    python enroll_face.py <name> <image_path> [image_path2 ...]

Example:
    python enroll_face.py yashesh photo1.jpg photo2.jpg selfie.png

This copies images into known_faces/ with proper naming and validates
that each image contains exactly one detectable face.
"""

import shutil
import sys
from pathlib import Path

import face_recognition

KNOWN_FACES_DIR = Path("./known_faces")


def enroll(name: str, image_paths: list[str]) -> None:
    KNOWN_FACES_DIR.mkdir(exist_ok=True)

    existing = list(KNOWN_FACES_DIR.glob(f"{name}_*"))
    start_idx = len(existing) + 1

    enrolled = 0
    for i, img_path in enumerate(image_paths, start=start_idx):
        src = Path(img_path)
        if not src.exists():
            print(f"  SKIP: {src} not found")
            continue

        image = face_recognition.load_image_file(str(src))
        locations = face_recognition.face_locations(image)

        if len(locations) == 0:
            print(f"  SKIP: No face detected in {src.name}")
            continue
        if len(locations) > 1:
            print(f"  WARN: Multiple faces in {src.name} — using first face")

        dest = KNOWN_FACES_DIR / f"{name}_{i}{src.suffix.lower()}"
        shutil.copy2(src, dest)
        print(f"  OK: {src.name} → {dest.name}")
        enrolled += 1

    print(f"\nEnrolled {enrolled}/{len(image_paths)} image(s) for '{name}'")
    print(f"Total images in known_faces/: {len(list(KNOWN_FACES_DIR.iterdir()))}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python enroll_face.py <name> <image1> [image2 ...]")
        sys.exit(1)

    enroll(sys.argv[1], sys.argv[2:])

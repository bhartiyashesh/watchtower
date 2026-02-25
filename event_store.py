"""
EventStore: Single shared database interface for the Smart Lock Analytics Platform.

This module wraps an aiosqlite connection in WAL mode and provides async
write/read methods for events, detections, and thumbnail management. Every
downstream phase (object detection, pipeline integration, Telegram alerts,
web dashboard) depends on this module for structured event storage.

Schema overview:
    events      — one row per Ring doorbell event (motion, ding)
    detections  — YOLO detection results linked to an event
    persons     — known household members enrolled for face recognition
"""

import asyncio
import io
import logging
from pathlib import Path

import aiosqlite
from PIL import Image

logger = logging.getLogger(__name__)


class EventStore:
    """Async SQLite interface for smart lock events, detections, and thumbnails."""

    def __init__(self, db_path: str, thumbnails_dir: str = "thumbnails") -> None:
        """
        Initialize EventStore with database and thumbnail storage paths.

        Args:
            db_path: Filesystem path to the SQLite database file.
            thumbnails_dir: Directory where JPEG thumbnails will be stored.
        """
        self.db_path = db_path
        self.thumbnails_dir = Path(thumbnails_dir)
        self.db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """
        Open the database connection, configure WAL mode, and create schema.

        PRAGMAs are applied before any DDL statements to ensure WAL mode is
        active and foreign keys are enforced from the start.
        """
        # Ensure thumbnail storage directory exists
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)

        # Open connection
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row

        # Apply PRAGMAs BEFORE any DDL — order matters for WAL mode
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.commit()

        # Create tables
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id      TEXT    NOT NULL DEFAULT 'front_door',
                recorded_at    TEXT    NOT NULL,
                event_type     TEXT    NOT NULL DEFAULT 'motion',
                recording_id   TEXT,
                person_name    TEXT,
                face_confidence REAL,
                face_distance  REAL,
                unlock_granted INTEGER NOT NULL DEFAULT 0,
                door_action    TEXT    DEFAULT 'none',
                thumbnail_path TEXT,
                alert_sent     INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS detections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                label       TEXT    NOT NULL,
                confidence  REAL    NOT NULL,
                bbox_x1     REAL,
                bbox_y1     REAL,
                bbox_x2     REAL,
                bbox_y2     REAL
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS persons (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL UNIQUE,
                display_name TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Create indexes for common query patterns
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_recorded_at ON events(recorded_at DESC)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_camera_id ON events(camera_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_person_name ON events(person_name)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_detections_event_id ON detections(event_id)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_detections_label ON detections(label)"
        )

        await self.db.commit()
        logger.info("EventStore initialized: %s", self.db_path)

    def save_thumbnail(self, frame_bytes: bytes, event_timestamp: str) -> str | None:
        """
        Save a video frame as a JPEG thumbnail to the thumbnails directory.

        This is a synchronous method because Pillow image operations are
        CPU-bound and should complete before the database write. Thumbnail
        failures are non-fatal — the event is always stored regardless.

        Args:
            frame_bytes: Raw image bytes (any PIL-supported format).
            event_timestamp: ISO 8601 timestamp used to build the filename.

        Returns:
            Relative path string (e.g. "thumbnails/2026-02-25T10-30-00.jpg")
            or None if any error occurs.
        """
        try:
            # Sanitize timestamp for filesystem use
            safe_ts = event_timestamp.replace(":", "-").replace(".", "-")
            filename = f"{safe_ts}.jpg"
            path = self.thumbnails_dir / filename

            img = Image.open(io.BytesIO(frame_bytes))
            img.save(str(path), "JPEG", quality=85)

            relative = str(self.thumbnails_dir / filename)
            logger.debug("Thumbnail saved: %s", relative)
            return relative
        except Exception:
            logger.exception("Failed to save thumbnail for timestamp %s", event_timestamp)
            return None

    async def write_event(
        self,
        camera_id: str,
        recorded_at: str,
        recording_id: str | None = None,
        event_type: str = "motion",
        person_name: str | None = None,
        face_confidence: float | None = None,
        face_distance: float | None = None,
        unlock_granted: bool = False,
        door_action: str = "none",
        thumbnail_path: str | None = None,
        detections: list[dict] | None = None,
    ) -> int:
        """
        Write an event (and optional detections) to the database atomically.

        Args:
            camera_id: Identifier for the Ring camera (e.g. 'front_door').
            recorded_at: ISO 8601 UTC timestamp of when the event occurred.
            recording_id: Ring recording ID (nullable).
            event_type: 'motion' or 'ding'.
            person_name: Recognized person's name, or None for stranger.
            face_confidence: Derived confidence score (max(0.0, 1.0 - face_distance)).
            face_distance: Raw euclidean distance from face_recognition library.
            unlock_granted: True if door was unlocked for this event.
            door_action: 'unlocked', 'locked', or 'none'.
            thumbnail_path: Relative path to saved JPEG thumbnail.
            detections: List of YOLO detection dicts with keys:
                        label, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2.

        Returns:
            Integer event_id of the newly inserted event row.
        """
        cursor = await self.db.execute(
            """
            INSERT INTO events (
                camera_id, recorded_at, event_type, recording_id,
                person_name, face_confidence, face_distance,
                unlock_granted, door_action, thumbnail_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                camera_id,
                recorded_at,
                event_type,
                recording_id,
                person_name,
                face_confidence,
                face_distance,
                1 if unlock_granted else 0,
                door_action,
                thumbnail_path,
            ),
        )
        event_id = cursor.lastrowid

        if detections:
            await self.db.executemany(
                """
                INSERT INTO detections (event_id, label, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event_id,
                        d["label"],
                        d["confidence"],
                        d.get("bbox_x1"),
                        d.get("bbox_y1"),
                        d.get("bbox_x2"),
                        d.get("bbox_y2"),
                    )
                    for d in detections
                ],
            )

        await self.db.commit()
        logger.debug("Wrote event %d (camera=%s, person=%s)", event_id, camera_id, person_name)
        return event_id

    async def get_event(self, event_id: int) -> dict | None:
        """
        Retrieve a single event with its associated detections.

        Args:
            event_id: Primary key of the event to retrieve.

        Returns:
            Dict with all event fields plus a 'detections' list, or None if
            the event does not exist.
        """
        async with self.db.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        event = dict(row)

        async with self.db.execute(
            "SELECT * FROM detections WHERE event_id = ?", (event_id,)
        ) as cursor:
            detection_rows = await cursor.fetchall()

        event["detections"] = [dict(r) for r in detection_rows]
        return event

    async def get_recent_events(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """
        Retrieve recent events ordered by recorded_at descending.

        Args:
            limit: Maximum number of events to return.
            offset: Number of events to skip (for pagination).

        Returns:
            List of event dicts, each including a nested 'detections' list.
        """
        async with self.db.execute(
            "SELECT * FROM events ORDER BY recorded_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()

        events = []
        for row in rows:
            event = dict(row)
            async with self.db.execute(
                "SELECT * FROM detections WHERE event_id = ?", (event["id"],)
            ) as det_cursor:
                det_rows = await det_cursor.fetchall()
            event["detections"] = [dict(r) for r in det_rows]
            events.append(event)

        return events

    async def close(self) -> None:
        """Close the database connection and release resources."""
        if self.db is not None:
            await self.db.close()
            self.db = None
            logger.info("EventStore connection closed")

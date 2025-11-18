"""
Background polling thread for Redis Streams.

Provides a managed thread that polls Redis Streams for new messages and signals
when updates are available, triggering UI refreshes.
"""

import logging
import threading
import time
from typing import Any

import redis
import streamlit as st

from quadracode_contracts import MessageEnvelope

logger = logging.getLogger(__name__)


class PollingThread:
    """
    Background thread that polls a Redis mailbox for new messages.
    
    Uses blocking XREAD to efficiently wait for new messages without spinning.
    Signals the main thread when new messages arrive.
    """
    
    def __init__(
        self,
        client: redis.Redis,
        mailbox: str,
        chat_id: str,
        last_id: str = "0-0",
        block_ms: int = 2000,
    ):
        """
        Initialize the polling thread.
        
        Args:
            client: Redis client instance
            mailbox: Mailbox stream key to monitor
            chat_id: Chat ID to filter messages
            last_id: Starting message ID (exclusive)
            block_ms: Milliseconds to block on XREAD (0 = block forever)
        """
        self.client = client
        self.mailbox = mailbox
        self.chat_id = chat_id
        self.last_id = last_id
        self.block_ms = block_ms
        
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._new_messages_event = threading.Event()
        self._lock = threading.Lock()
        self._new_messages: list[MessageEnvelope] = []
        self._running = False
    
    def start(self) -> None:
        """Start the background polling thread."""
        if self._running:
            logger.warning("Polling thread already running")
            return
        
        self._stop_event.clear()
        self._new_messages_event.clear()
        self._running = True
        
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"Started polling thread for mailbox {self.mailbox}")
    
    def stop(self) -> None:
        """Stop the background polling thread."""
        if not self._running:
            return
        
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        self._running = False
        logger.info(f"Stopped polling thread for mailbox {self.mailbox}")
    
    def _poll_loop(self) -> None:
        """Main polling loop running in background thread."""
        while not self._stop_event.is_set():
            try:
                # Blocking XREAD - waits for new messages
                responses = self.client.xread(
                    {self.mailbox: self.last_id},
                    count=10,
                    block=self.block_ms,
                )
                
                if not responses:
                    # Timeout occurred, no new messages
                    continue
                
                # Process new messages
                new_messages: list[MessageEnvelope] = []
                new_last_id = self.last_id
                
                for stream_key, entries in responses:
                    if stream_key != self.mailbox:
                        continue
                    
                    for entry_id, fields in entries:
                        envelope = MessageEnvelope.from_stream_fields(fields)
                        
                        # Filter by chat_id
                        payload = envelope.payload or {}
                        if payload.get("chat_id") != self.chat_id:
                            # Update last_id even if not our chat
                            if entry_id > new_last_id:
                                new_last_id = entry_id
                            continue
                        
                        new_messages.append(envelope)
                        if entry_id > new_last_id:
                            new_last_id = entry_id
                
                # Update state if we got relevant messages
                if new_messages:
                    with self._lock:
                        self._new_messages.extend(new_messages)
                        self.last_id = new_last_id
                        self._new_messages_event.set()
                        logger.debug(f"Received {len(new_messages)} new messages")
                elif new_last_id != self.last_id:
                    # Update last_id even if no messages matched our chat
                    with self._lock:
                        self.last_id = new_last_id
            
            except redis.RedisError as e:
                logger.error(f"Redis error in polling thread: {e}")
                # Sleep before retrying to avoid tight loop on persistent errors
                time.sleep(2.0)
            except Exception as e:
                logger.error(f"Unexpected error in polling thread: {e}", exc_info=True)
                # Sleep before retrying
                time.sleep(2.0)
    
    def has_new_messages(self) -> bool:
        """Check if new messages are available."""
        return self._new_messages_event.is_set()
    
    def get_new_messages(self) -> tuple[list[MessageEnvelope], str]:
        """
        Retrieve and clear new messages.
        
        Returns:
            Tuple of (messages, last_id)
        """
        with self._lock:
            messages = self._new_messages.copy()
            self._new_messages.clear()
            self._new_messages_event.clear()
            return messages, self.last_id
    
    def update_mailbox(self, mailbox: str) -> None:
        """Update the mailbox being monitored (e.g., when switching modes)."""
        with self._lock:
            if mailbox != self.mailbox:
                self.mailbox = mailbox
                # Reset last_id to get recent messages from new mailbox
                try:
                    latest = self.client.xrevrange(mailbox, count=1)
                    self.last_id = latest[0][0] if latest else "0-0"
                except Exception:
                    self.last_id = "0-0"
                logger.info(f"Updated polling thread to mailbox {mailbox}")


def get_polling_thread(
    client: redis.Redis,
    mailbox: str,
    chat_id: str,
    last_id: str = "0-0",
) -> PollingThread:
    """
    Get or create a polling thread for the current session.
    
    Manages thread lifecycle via Streamlit session state. Ensures only one
    thread runs per session and handles cleanup properly.
    
    Args:
        client: Redis client
        mailbox: Mailbox to monitor
        chat_id: Chat ID to filter
        last_id: Starting message ID
    
    Returns:
        Polling thread instance
    """
    # Initialize polling thread in session state if needed
    if "polling_thread" not in st.session_state:
        thread = PollingThread(client, mailbox, chat_id, last_id)
        st.session_state.polling_thread = thread
        thread.start()
    else:
        thread = st.session_state.polling_thread
        # Update mailbox if it changed (e.g., mode switch)
        if thread.mailbox != mailbox:
            thread.update_mailbox(mailbox)
    
    return thread


def stop_polling_thread() -> None:
    """Stop and cleanup the polling thread from session state."""
    if "polling_thread" in st.session_state:
        thread = st.session_state.polling_thread
        thread.stop()
        del st.session_state.polling_thread
        logger.info("Cleaned up polling thread")


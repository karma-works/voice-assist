import unittest
import asyncio
import json

from fastapi.testclient import TestClient
from pipecat.frames.frames import InterruptionFrame, LLMFullResponseEndFrame, TTSAudioRawFrame

from src.main import app
from src.processors import AudioInputProcessor, InterruptionProcessor, TextOutputProcessor
from src.session import BrowserWebSocketSerializer


class ProcessorTests(unittest.TestCase):
    def test_audio_processor_rejects_odd_pcm_payloads(self):
        processor = AudioInputProcessor()

        self.assertTrue(processor.observe_pcm16(b"\x00\x00"))
        self.assertFalse(processor.observe_pcm16(b"\x00"))
        self.assertEqual(processor.metrics.frames, 2)
        self.assertEqual(processor.metrics.dropped_frames, 1)

    def test_interruption_classifier_distinguishes_backchannels(self):
        processor = InterruptionProcessor()

        self.assertEqual(processor.classify_text("uh-huh"), "backchannel")
        self.assertEqual(processor.classify_text("stop"), "interruption")
        self.assertEqual(processor.classify_text("Tuesday works"), "speech")

    def test_text_processor_strips_markdown_and_formats_phone(self):
        processor = TextOutputProcessor()

        self.assertEqual(processor.normalize("**Hi** [there](https://example.com)"), "Hi there")
        self.assertEqual(processor.format_phone_readback("+491711234567"), "plus 4 9, 1 7 1 1, 2 3 4, 5 6 7")
        self.assertEqual(processor.format_phone_readback("01711234567"), "0 1 7 1, 1 2 3 4, 5 6 7")


class BrowserWebSocketSerializerTests(unittest.TestCase):
    def test_binary_browser_audio_deserializes_to_pipecat_audio_frame(self):
        async def run():
            serializer = BrowserWebSocketSerializer().serializer

            frame = await serializer.deserialize(b"\x00\x00")

            self.assertEqual(type(frame).__name__, "InputAudioRawFrame")
            self.assertEqual(frame.sample_rate, 16000)
            self.assertEqual(frame.num_channels, 1)
            self.assertEqual(frame.audio, b"\x00\x00")

        asyncio.run(run())

    def test_interrupt_control_message_round_trips_to_browser_json(self):
        async def run():
            serializer = BrowserWebSocketSerializer().serializer

            frame = await serializer.deserialize(json.dumps({"type": "interrupt"}))
            payload = await serializer.serialize(frame)

            self.assertIsInstance(frame, InterruptionFrame)
            self.assertEqual(json.loads(payload), {"type": "interrupted"})

        asyncio.run(run())

    def test_output_audio_and_turn_complete_match_browser_protocol(self):
        async def run():
            serializer = BrowserWebSocketSerializer().serializer

            audio = await serializer.serialize(
                TTSAudioRawFrame(audio=b"\x01\x02", sample_rate=24000, num_channels=1)
            )
            complete = await serializer.serialize(LLMFullResponseEndFrame())

            self.assertEqual(audio, b"\x01\x02")
            self.assertEqual(json.loads(complete), {"type": "turn_complete"})

        asyncio.run(run())


class RouteTests(unittest.TestCase):
    def test_health_route(self):
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()

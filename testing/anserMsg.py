import argparse
import asyncio
import json
import logging
import math
import os
import platform
import threading

import cv2
import numpy
from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    RTCConfiguration,
    RTCIceServer
)
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder, MediaPlayer, MediaRelay
from aiortc.contrib.signaling import BYE, add_signaling_arguments, create_signaling
from aiortc.rtcrtpsender import RTCRtpSender
from av import VideoFrame
import requests

ROOT = os.path.dirname(__file__)


relay = None
webcam = None
Cap = None


class FlagVideoStreamTrack(VideoStreamTrack):
    """
    A video track that returns an animated flag.
    """

    def __init__(self):
        super().__init__()  # don't forget this!
        self.counter = 0
        height, width = 480, 640

        # generate flag
        data_bgr = numpy.hstack(
            [
                self._create_rectangle(
                    width=213, height=480, color=(255, 0, 0)
                ),  # blue
                self._create_rectangle(
                    width=214, height=480, color=(255, 255, 255)
                ),  # white
                self._create_rectangle(width=213, height=480, color=(0, 0, 255)),  # red
            ]
        )

        # shrink and center it
        M = numpy.float32([[0.5, 0, width / 4], [0, 0.5, height / 4]])
        data_bgr = cv2.warpAffine(data_bgr, M, (width, height))

        # compute animation
        omega = 2 * math.pi / height
        id_x = numpy.tile(numpy.array(range(width), dtype=numpy.float32), (height, 1))
        id_y = numpy.tile(
            numpy.array(range(height), dtype=numpy.float32), (width, 1)
        ).transpose()

        self.frames = []
        for k in range(30):
            phase = 2 * k * math.pi / 30
            map_x = id_x + 10 * numpy.cos(omega * id_x + phase)
            map_y = id_y + 10 * numpy.sin(omega * id_x + phase)
            self.frames.append(
                VideoFrame.from_ndarray(
                    cv2.remap(data_bgr, map_x, map_y, cv2.INTER_LINEAR), format="bgr24"
                )
            )

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        frame = self.frames[self.counter % 30]
        frame.pts = pts
        frame.time_base = time_base
        self.counter += 1
        return frame

    def _create_rectangle(self, width, height, color):
        data_bgr = numpy.zeros((height, width, 3), numpy.uint8)
        data_bgr[:, :] = color
        return data_bgr

class CV2VideoStreamTrack(VideoStreamTrack):
    """
    A video track that returns an animated flag.
    """

    def __init__(self):
        super().__init__()  # don't forget this!

        video_path = "/dev/video0"
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)

    async def recv(self):
        # Read frames from the video file and convert them to RTCVideoFrames
        ret, img = self.cap.read()
        if ret:
            pts, time_base = await self.next_timestamp()
            frame = VideoFrame.from_ndarray(img, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base
            await asyncio.sleep(1/30)
            # cv2.putText(frame, 'Write By CV2', (50,50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,255),2, cv2.LINE_4)
            return frame
        else:
            # Video ended, close the connection
            self.cap.release()
            raise ConnectionError("Video stream ended")
        
def create_local_tracks(cap_type=None):
    global relay, webcam, Cap

    if cap_type is None:
        options = {"framerate": "30", "video_size": "640x480"}
        if relay is None:
            if platform.system() == "Darwin":
                webcam = MediaPlayer(
                    "default:none", format="avfoundation", options=options
                )
            elif platform.system() == "Windows":
                webcam = MediaPlayer(
                    "video=Integrated Camera", format="dshow", options=options
                )
            else:
                webcam = MediaPlayer("/dev/video0", format="v4l2", options=options)
            relay = MediaRelay()
        return None, relay.subscribe(webcam.video)
        # return None, relay.subscribe(FlagVideoStreamTrack())
    elif cap_type == "cv2":
        if relay is None:
            relay = MediaRelay()
        if Cap is None:
            Cap = CV2VideoStreamTrack()
        return None, relay.subscribe(Cap)
        # return None, relay.subscribe(FlagVideoStreamTrack())
    else:
        print(f"capture type({cap_type} does not available.)")
        return None, None

def force_codec(pc, sender, forced_codec):
    kind = forced_codec.split("/")[0]
    codecs = RTCRtpSender.getCapabilities(kind).codecs
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences(
        [codec for codec in codecs if codec.mimeType == forced_codec]
    )

async def wait_for_ice_gathering_complete(pc):
    async def check_state():
        while pc.iceGatheringState != 'complete':
            await asyncio.sleep(0.1)  # Adjust sleep time as needed
        return

    if pc.iceGatheringState == 'complete':
        return
    
    await check_state()

async def liveView():
    global Cap
    keep_looping = threading.Event()
    keep_looping.set()
    while keep_looping.is_set():
        try:
            url = "http://localhost:8888/offer"
            # url = "https://test.api.longansorter.aiindustries.co/offer"
            response = requests.get(url=url)
            if response.status_code == 200:
                _json = json.loads(response.text)
                offer = RTCSessionDescription(sdp=_json.get("sdp"),type=_json.get("type"))

                config = RTCConfiguration(
                    iceServers=[
                        RTCIceServer(urls=['stun:stun.l.google.com:19302'])
                        ]
                )

                pc = RTCPeerConnection(config)
                # pc = RTCPeerConnection()

                # pcs.add(pc)

                @pc.on("connectionstatechange")
                async def on_connectionstatechange():
                    print("Connection state is %s" % pc.connectionState)
                    if pc.connectionState in ["failed", "closed"]:
                        await pc.close()
                        pcs.discard(pc)

                # open media source
                audio, video = create_local_tracks(cap_type="cv2") # default=None (for Platform Video)
                # audio, video = create_local_tracks()

                if audio:
                    audio_sender = pc.addTrack(audio)
                    # if args.audio_codec:
                    #     force_codec(pc, audio_sender, args.audio_codec)
                    # elif args.play_without_decoding:
                    #     raise Exception("You must specify the audio codec using --audio-codec")

                if video:
                    video_sender = pc.addTrack(video)
                    force_codec(pc, video_sender, "video/hh")
                    # if args.video_codec:
                    #     force_codec(pc, video_sender, args.video_codec)
                    # elif args.play_without_decoding:
                    #     raise Exception("You must specify the video codec using --video-codec")

                # pc.addTrack(FlagVideoStreamTrack)
                await pc.setRemoteDescription(offer)

                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await wait_for_ice_gathering_complete(pc)
                
                url = "http://localhost:8888/stream"
                # url = "https://test.api.longansorter.aiindustries.co/stream"
                data = json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
                headers = {'Content-Type': 'application/json'}
                response = requests.post(url, data=data, headers=headers)
                
                pcs.add(pc)
                await asyncio.sleep(3)
            else:
                await asyncio.sleep(3)
                if (len(pcs) == 0) and Cap is not None:
                    Cap.cap.release()
                    Cap = None
                if (len(pcs) == 0):
                    # keep_looping.clear()
                    pass

        except Exception as e:
            print("error -",e)
            await asyncio.sleep(3)
            pass

async def run(role):
    # config = {
    #     "sdpSemantics": "unified-plan",
    #     "iceServers" : [{ "urls": ['stun:stun.l.google.com:19302'] }]
    # }
    config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=['stun:stun.l.google.com:19302'])
            ]
    )

    pc = RTCPeerConnection(config)
    # pc = RTCPeerConnection()
    pcs.add(pc)

    while True:
        try:
            url = "http://localhost:8888/offer"
            # url = "https://test.api.longansorter.aiindustries.co/offer"
            response = requests.get(url=url)
            if response.status_code == 200:
                _json = json.loads(response.text)
                offer = RTCSessionDescription(sdp=_json.get("sdp"),type=_json.get("type"))
                break
        except Exception as e:
            pass

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    # open media source
    audio, video = create_local_tracks(cap_type="cv2") # default=None (for Platform Video)

    if audio:
        audio_sender = pc.addTrack(audio)
        # if args.audio_codec:
        #     force_codec(pc, audio_sender, args.audio_codec)
        # elif args.play_without_decoding:
        #     raise Exception("You must specify the audio codec using --audio-codec")

    if video:
        video_sender = pc.addTrack(video)
        force_codec(pc, video_sender, "video/hh")
        # if args.video_codec:
        #     force_codec(pc, video_sender, args.video_codec)
        # elif args.play_without_decoding:
        #     raise Exception("You must specify the video codec using --video-codec")

    # pc.addTrack(FlagVideoStreamTrack)
    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    await wait_for_ice_gathering_complete(pc)

    url = "http://localhost:8888/stream"
    # url = "https://test.api.longansorter.aiindustries.co/stream"
    data = json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, data=data, headers=headers)
    await asyncio.sleep(3)
    print("slept")
    while True:
        await asyncio.sleep(1)
        pass


async def infinate_loop():
    while True:
        pass

pcs = set()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Video stream from the command line")
    # parser.add_argument("role", choices=["offer", "answer"])
    parser.add_argument("--play-from", help="Read the media from a file and sent it.")
    parser.add_argument("--record-to", help="Write received media to a file.")
    parser.add_argument("--verbose", "-v", action="count")
    add_signaling_arguments(parser)
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # create signaling and peer connection
    signaling = create_signaling(args)
    # pc = RTCPeerConnection()
    

    # create media source
    if args.play_from:
        player = MediaPlayer(args.play_from)
    else:
        player = None

    # create media sink
    if args.record_to:
        recorder = MediaRecorder(args.record_to)
    else:
        recorder = MediaBlackhole()

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(
            # run(
            #     role="offer",
            # )
            liveView()
        )
    except KeyboardInterrupt:
        pass
    finally:
        # cleanup
        # loop.run_until_complete(recorder.stop())
        # loop.run_until_complete(signaling.close())
        # loop.run_until_complete(pc.close())
        # loop.run_until_complete(
        #     run(
        #         role="offer",
        #     )
        # )
        # loop.run_until_complete(infinate_loop())
        pass

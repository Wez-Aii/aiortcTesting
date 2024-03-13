import argparse
import asyncio
import json
import logging
import math
import os
import platform
import ssl

from aiohttp import web

import cv2
import numpy
from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder, MediaRelay
from aiortc.contrib.signaling import BYE, add_signaling_arguments, create_signaling
from aiortc.rtcrtpsender import RTCRtpSender
from av import VideoFrame
import requests


ROOT = os.path.dirname(__file__)


relay = None
webcam = None

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

        # self.frames = []
        # for k in range(30):
        #     phase = 2 * k * math.pi / 30
        #     map_x = id_x + 10 * numpy.cos(omega * id_x + phase)
        #     map_y = id_y + 10 * numpy.sin(omega * id_x + phase)
        #     self.frames.append(
        #         VideoFrame.from_ndarray(
        #             cv2.remap(data_bgr, map_x, map_y, cv2.INTER_LINEAR), format="bgr24"
        #         )
        #     )
        
        video_path = "/dev/video0"
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)

    # async def recv(self):
    #     pts, time_base = await self.next_timestamp()

    #     frame = self.frames[self.counter % 30]
    #     frame.pts = pts
    #     frame.time_base = time_base
    #     self.counter += 1
    #     return frame

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
        
    def _create_rectangle(self, width, height, color):
        data_bgr = numpy.zeros((height, width, 3), numpy.uint8)
        data_bgr[:, :] = color
        return data_bgr
    


def create_local_tracks(play_from, decode):
    global relay, webcam

    if play_from:
        player = MediaPlayer(play_from, decode=decode)
        return player.audio, player.video
    else:
        options = {"framerate": "30", "video_size": "640x480"}
        if relay is None:
            # if platform.system() == "Darwin":
            #     webcam = MediaPlayer(
            #         "default:none", format="avfoundation", options=options
            #     )
            # elif platform.system() == "Windows":
            #     webcam = MediaPlayer(
            #         "video=Integrated Camera", format="dshow", options=options
            #     )
            # else:
            #     webcam = MediaPlayer("/dev/video0", format="v4l2", options=options)
            relay = MediaRelay()
        # return None, relay.subscribe(webcam)
        return None, relay.subscribe(FlagVideoStreamTrack())
        # return None, FlagVideoStreamTrack()


def force_codec(pc, sender, forced_codec):
    kind = forced_codec.split("/")[0]
    codecs = RTCRtpSender.getCapabilities(kind).codecs
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences(
        [codec for codec in codecs if codec.mimeType == forced_codec]
    )


async def index(request):
    # content = open(os.path.join(ROOT, "index.html"), "r").read()
    # return web.Response(content_type="text/html", text=content)
    # config = {
    #     "sdpSemantics": "unified-plan",
    #     "iceServers" : [{ "urls": ['stun:stun.l.google.com:19302'] }]
    # }
    # config = RTCConfiguration(
    #     iceServers=[
    #         RTCIceServer(urls=['stun:stun.l.google.com:19302'])
    #         ]
    # )

    # pc = RTCPeerConnection(config)
    pc = RTCPeerConnection()
    pcs.add(pc)

    while True:
        try:
            url = "http://192.168.1.26:8080/offer"
            response = requests.get(url=url)
            if response.status_code == 200:
                _json = response.json()
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
    # audio, video = create_local_tracks(
    #     None, None
    # )

    # if audio:
    #     audio_sender = pc.addTrack(audio)
    #     # if args.audio_codec:
    #     #     force_codec(pc, audio_sender, args.audio_codec)
    #     # elif args.play_without_decoding:
    #     #     raise Exception("You must specify the audio codec using --audio-codec")

    # if video:
    #     video_sender = pc.addTrack(video)
    #     force_codec(pc, video_sender, "video/hh")
    #     # if args.video_codec:
    #     #     force_codec(pc, video_sender, args.video_codec)
    #     # elif args.play_without_decoding:
    #     #     raise Exception("You must specify the video codec using --video-codec")

    pc.addTrack(FlagVideoStreamTrack)
    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    # await wait_for_ice_gathering_complete(pc)

    url = "http://192.168.1.26:8080/stream"
    data = json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, data=data, headers=headers)
    await asyncio.sleep(1)
    print("slept")

    return web.Response(
        text="ok",
    )



async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    # open media source
    audio, video = create_local_tracks(
        args.play_from, decode=not args.play_without_decoding
    )

    if audio:
        audio_sender = pc.addTrack(audio)
        if args.audio_codec:
            force_codec(pc, audio_sender, args.audio_codec)
        elif args.play_without_decoding:
            raise Exception("You must specify the audio codec using --audio-codec")

    if video:
        video_sender = pc.addTrack(video)
        if args.video_codec:
            force_codec(pc, video_sender, args.video_codec)
        elif args.play_without_decoding:
            raise Exception("You must specify the video codec using --video-codec")

    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


pcs = set()


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC webcam demo")
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument("--play-from", help="Read the media from a file and sent it.")
    parser.add_argument(
        "--play-without-decoding",
        help=(
            "Read the media without decoding it (experimental). "
            "For now it only works with an MPEGTS container with only H.264 video."
        ),
        action="store_true",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for HTTP server (default: 8080)"
    )
    parser.add_argument("--verbose", "-v", action="count")
    parser.add_argument(
        "--audio-codec", help="Force a specific audio codec (e.g. audio/opus)"
    )
    parser.add_argument(
        "--video-codec", help="Force a specific video codec (e.g. video/H264)"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.cert_file:
        ssl_context = ssl.SSLContext()
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    else:
        ssl_context = None

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)

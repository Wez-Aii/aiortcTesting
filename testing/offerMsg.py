import argparse
import asyncio
import json
import logging
import math
import os

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

def create_local_tracks(play_from, decode):
    global relay, webcam

    if play_from:
        player = MediaPlayer(play_from, decode=decode)
        return player.audio, player.video
    else:
        options = {"framerate": "30", "video_size": "640x480"}
        if relay is None:
            webcam = MediaPlayer("/dev/video0", format="v4l2", options=options)
            relay = MediaRelay()
        return None, relay.subscribe(webcam.video)

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

async def run(pc, player, recorder, signaling, role):
    # pc.addTransceiver('video', {"direction":"sendrecv"})
    # pc.addTransceiver('audio', {"direction":"sendrecv"})

    def add_tracks():
        if player and player.audio:
            pc.addTrack(player.audio)

        if player and player.video:
            pc.addTrack(player.video)
        else:
            audio, video = create_local_tracks(
                None, decode=None
            )

            if audio:
                audio_sender = pc.addTrack(audio)
                # if args.audio_codec:
                #     force_codec(pc, audio_sender, args.audio_codec)
                # elif args.play_without_decoding:
                #     raise Exception("You must specify the audio codec using --audio-codec")

            if video:
                video_sender = pc.addTrack(video)
                force_codec(pc, video_sender, "video/H264")
                # if args.video_codec:
                #     force_codec(pc, video_sender, args.video_codec)
                # elif args.play_without_decoding:
                #     raise Exception("You must specify the video codec using --video-codec")

    add_tracks()
    await pc.setLocalDescription(await pc.createOffer())
    await wait_for_ice_gathering_complete(pc)
    await signaling.send(pc.localDescription)

    offer_data = {
        'sdp': pc.localDescription.sdp,
        'type': pc.localDescription.type
    }

    while True:
        url = "http://192.168.1.26:8080/stream"
        data = json.dumps(offer_data)
        headers = {'Content-Type': 'application/json'}

        response = requests.post(url, data=data, headers=headers)
        if response.status_code == 200:
            _json = response.json()
            await pc.setRemoteDescription(RTCSessionDescription(sdp=_json.get("sdp"),type=_json.get("type")))

            @pc.on("connectionstatechange")
            async def on_connectionstatechange():
                print("Connection state is %s" % pc.connectionState)
                if pc.connectionState == "failed":
                    await pc.close()
                    # pcs.discard(pc)

            async def on_ice_candidate(candidate):
                print("candidate_data -", candidate_data)
                # Access candidate data
                candidate_data = {
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex
                    # Add any other relevant candidate properties here
                }

                # Process candidate data (e.g., send it to the client)

            # Add event handler for ICE candidate gathering
            pc.on("icecandidate", on_ice_candidate)
            break
        else:
            # print(response.status_code)
            await asyncio.sleep(1)
            pass


    # def add_tracks():
    #     if player and player.audio:
    #         pc.addTrack(player.audio)

    #     if player and player.video:
    #         pc.addTrack(player.video)
    #     else:
    #         pc.addTrack(FlagVideoStreamTrack())

    # @pc.on("track")
    # def on_track(track):
    #     print("Receiving %s" % track.kind)
    #     recorder.addTrack(track)

    # # connect signaling
    # await signaling.connect()

    # if role == "offer":
    #     # send offer
    #     add_tracks()
    #     await pc.setLocalDescription(await pc.createOffer())
    #     await signaling.send(pc.localDescription)

    # # consume signaling
    # while True:
    #     pass
        # obj = await signaling.receive()

        # if isinstance(obj, RTCSessionDescription):
        #     await pc.setRemoteDescription(obj)
        #     await recorder.start()

        #     if obj.type == "offer":
        #         # send answer
        #         add_tracks()
        #         await pc.setLocalDescription(await pc.createAnswer())
        #         await signaling.send(pc.localDescription)
        # elif isinstance(obj, RTCIceCandidate):
        #     await pc.addIceCandidate(obj)
        # elif obj is BYE:
        #     print("Exiting")
        #     break

async def infinate_loop():
    while True:
        pass

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
            run(
                pc=pc,
                player=player,
                recorder=recorder,
                signaling=signaling,
                role="offer",
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        # cleanup
        # loop.run_until_complete(recorder.stop())
        loop.run_until_complete(signaling.close())
        # loop.run_until_complete(pc.close())
        loop.run_until_complete(infinate_loop())

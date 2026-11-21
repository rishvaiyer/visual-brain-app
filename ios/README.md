# Visual Brain for iPhone and Meta glasses

An iPhone companion for a private Visual Brain library. Choose the iPhone camera
or phone-paired Meta glasses, compare what is in view with saved images, and
reopen the matching board. Both cameras feed the same `FrameSource` interface
and appearance-matching request.

## Open and build

Open `VisualBrain.xcodeproj` with Xcode 26.4 or newer. The app requires iOS 17.2
or newer and pins Meta's Device Access Toolkit to **1.1.0**. Select your own
Apple signing team when installing on your device. No team or credential is
included in this project.

The checked-in project can be regenerated with [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```sh
cd ios
xcodegen generate
xcodebuild -project VisualBrain.xcodeproj -scheme VisualBrain \
  -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

`META_APP_ID` defaults to `0` with an empty `CLIENT_TOKEN` for Meta's Developer
Mode setup. For a registered Meta app, supply your own values through local
build settings or an untracked `Local.xcconfig`, along with your signing team.
Do not commit access tokens or credentials. Configure the `visualbrain://`
callback consistently in your Meta app. The target includes Hotspot Configuration
and Access Wi-Fi Information entitlements for the toolkit’s camera transport. The toolkit connects through Meta AI;
it does not use a browser webcam or a direct Mac connection.

Use the [official toolkit setup](https://wearables.developer.meta.com/docs/getting-started-toolkit)
for pairing, Developer Mode, permissions and supported device software. The
app opens Meta AI for registration and camera permission. If that handoff
backgrounds Visual Brain, tap **Start looking** after returning; capture is
never resumed silently.

## Connect your library

Connect your own passphrase-protected **HTTPS** hosted library. Enter its root
address and passphrase in the app. Shared playgrounds are rejected before
camera startup. The library must expose the matching companion endpoints:

- `GET /api/glasses/status`: `private_library` and `voice_available`.
- `POST /api/login`: form-encoded `passphrase`, setting the library session cookie.
- `POST /api/camera-match`: JPEG `image_data_url`, returning `match_state` and
  `matches` with asset IDs, titles, board IDs and board names.
- `GET /api/assets/<id>/thumbnail`: an authenticated JPEG preview capped at 384 pixels.
- `POST /api/glasses/voice`: text to natural-voice audio, or an explicit unavailable response.

Every POST includes the configured HTTPS origin. Login uses an independent,
in-memory cookie session. In-app boards receive only that same session's
cookies and open only the connected origin. External browser sessions are
never read or copied.

The passphrase, session and camera frames are not written to disk. Only the
library address is remembered. Frames go to the connected library only during
explicit capture; **Stop**, backgrounding, changing cameras and disconnecting
cancel matching, clear the preview and stop both camera sources.

## Hands-free use

In Meta glasses mode, **Compare as I look** compares incoming frames at a
bounded cadence while capture is active. Turn it off for manual comparison.
Results say **Similar saved images** or **Visual neighbors**: appearance
similarity is not an object-identity guarantee.

Natural voice is **off by default**. Turning it on requires confirmation that
spoken image titles and board names go to the library's configured voice
service. Camera frames are not sent to that voice service. Audio plays through
the iPhone's selected output, including connected Bluetooth glasses; use the
audio-output picker if needed. A voice-service failure is visible and turns
voice off. There is no robotic text-to-speech fallback.

## Verification boundaries

Unsigned generic-device compilation checks Swift, the pinned toolkit API and
the app bundle. It cannot establish successful registration, physical glasses
streaming, camera permissions, Bluetooth playback or install eligibility for a
particular Meta account. Those remain real-device acceptance checks. No
hardware capture or simulator run is represented by a successful build.

The tests cover origin validation, match-response interpretation and restarting
the shared frame channel. Camera frames, passphrases and session cookies are
excluded from fixtures.

## Primary references

- [Meta DAT iOS 1.1.0](https://github.com/meta/meta-wearables-dat-ios/tree/1.1.0)
- [Meta CameraAccess sample](https://github.com/meta/meta-wearables-dat-ios/tree/main/samples/CameraAccess)
- [Meta developer documentation](https://wearables.developer.meta.com/docs/develop/)

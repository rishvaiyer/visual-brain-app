# Visual Brain Capture extension

This folder is an unpacked Chrome/Edge extension. If you downloaded it as a ZIP from the releases page, unzip it first and choose the extracted folder in **Load unpacked**. Chrome cannot load the ZIP itself.

This Manifest V3 extension adds two ways to save an image to your local Visual Brain board:

- Right-click an image, linked image tile, or styled visual and open **Save image to Visual Brain ▶**. Choose **Everything + Library** or a specific board. A specific board save also adds the image to Library.
- Hover over an image or visual tile and click the floating **Save to Visual Brain** button to save to Everything + Library. This also handles images added later by pages such as Pinterest.
- Hover an image and press **Option+Shift+S** (shown as **Alt+Shift+S** in Chrome's shortcut settings) to save it without clicking the floating button. Change the shortcut at `chrome://extensions/shortcuts` if it conflicts with another app.

The image-save hover button and context-menu action stay hidden on Visual Brain itself and on images served by its local library, since those images are already saved there.

The extension tries the source image file first. It recognizes ordinary images, linked image tiles, CSS backgrounds, and visible canvases, including in web page frames. If the image bytes are hidden or blocked, it captures the visible tab and sends the screenshot plus target rectangle to the local app, which stores only the cropped image. A clip may be lower resolution or include text over the image. The extension never silently imports a page or uses an AI service. Browser-internal pages and visuals with no detectable on-screen rectangle cannot be captured by this workflow.

## Load in Chrome or Edge

1. Open `chrome://extensions` in Chrome or `edge://extensions` in Edge.
2. Turn on **Developer mode**.
3. Choose **Load unpacked** and select this `extension` folder.
4. Open the extension popup and set the pairing key from the board. The default local backend URL is `http://127.0.0.1:8777`.
5. Hover over an image or right-click it to capture. After updating this unpacked folder, click the extension's **Reload** button on the Extensions page once so Chrome reads the new shortcut and menu code, then refresh the page with the image.

The extension does not install itself, publish captures, or send them anywhere except the configured local backend. The pairing key is stored in this browser profile's extension storage and sent only as the `X-Board-Key` header to the loopback backend.

## Backend request

The service worker makes `POST /api/capture` with `Content-Type: application/json` and `X-Board-Key`. Example body for a source-file capture:

```json
{
  "source_url": "https://example.com/article",
  "image_url": "https://cdn.example.com/image.jpg",
  "alt": "Sunlit room with a chair",
  "title": "Interior reference",
  "favorite": true,
  "board_id": "board_123 (only when a specific board is chosen)",
  "image_data_url": "data:image/jpeg;base64,..."
}
```

`favorite` is omitted unless the popup's default-favorite option is enabled. `image_data_url` is required on every request from the extension. The backend can return `{ "asset": ..., "item": ..., "duplicate": false }` or `{ "error": "..." }`.

For a visible clip, the request also includes `screen_crop` with the target rectangle and viewport size. The full screenshot is held only in memory for that request; the local server crops it before hashing or storing the asset.

## Permissions

The extension asks for access to all web pages so it can add the hover button and the background service worker can fetch image bytes across site origins. Web pages never make requests to the local backend; the extension service worker handles the capture after a user clicks the button or context-menu item. The configured backend URL is restricted to an HTTP loopback address (`localhost`, `127.x.x.x`, or `::1`).

Image fetches omit site cookies. Some sites require an authenticated image request; in that case capture fails visibly and no item is sent to the board.

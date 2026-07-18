# Live2D browser runtime notices

- PixiJS 8.13.1 is distributed under the MIT License. See `LICENSE-pixi-8.txt`.
- untitled-pixi-live2d-engine 1.3.1 is distributed under the MIT License. See `LICENSE-untitled-pixi-live2d-engine.txt`. The vendored browser bundle contains a local compatibility fix in `uploadTextureForRender`: PixiJS 8.13.1 texture sources no longer expose the `_gpuData` field expected by the upstream bundle, so the renderer now binds the texture through PixiJS's public texture system on each Live2D render pass.
- `live2dcubismcore-5.0.0.min.js` is the official hosted Live2D Cubism Core 5 for Web downloaded from `https://cubism.live2d.com/sdk-web/core/05/live2dcubismcore.min.js` on 2026-07-14 (SHA-256 `25AE938CB4FE282CE189B357BCC97E603D1E1F7EC78BF04150D401C23CDC792F`). It retains the Live2D Inc. copyright and license notice embedded in the file and is pinned locally so the Evelyn page does not depend on a CDN at runtime.

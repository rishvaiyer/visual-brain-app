# Local model-viewer guard

The bundled `model-viewer.min.js` has one local change. Its AR renderer's `onUpdateScene` now checks that `presentedScene` exists before adding the placement menu. Changing a model's scale before entering AR otherwise throws `Cannot read properties of null (reading 'add')` in the browser. The guard skips that menu update while no AR scene is active; it does not alter model rendering or AR presentation.

`tests/ui_free_features.cjs` changes the displayed object's real height twice and fails if the viewer throws or its scale drifts. Keep this guard or verify that an upstream replacement passes that browser test.

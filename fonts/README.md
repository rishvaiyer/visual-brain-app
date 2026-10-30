# Bundled typefaces

Five open fonts you can type with on any board. Three are modified open fonts with new names; two are unmodified copies.

| Saved font | Source | Treatment |
| --- | --- | --- |
| Cut Ink | [Permanent Marker](https://github.com/google/fonts/tree/main/apache/permanentmarker) | Renamed, condensed, raised, angled and varied by glyph for a heavy ink cut feel. |
| Tall Stamp | [Patrick Hand SC](https://github.com/google/fonts/tree/main/ofl/patrickhandsc) | Renamed, narrowed, raised and varied by glyph for a tall hand lettered feel. |
| Sunday Sign | [Balsamiq Sans](https://github.com/google/fonts/tree/main/ofl/balsamiqsans) | Renamed and gently reshaped for a casual sign lettering feel. |
| Kalam Bold | [Kalam](https://github.com/google/fonts/tree/main/ofl/kalam) | Unmodified open font, a similar brush option. |
| Cabin Sketch | [Cabin Sketch](https://github.com/google/fonts/tree/main/ofl/cabinsketch) | Unmodified open font, a textured sign option. |

All five font files are in `generated/` and their source licenses are in `licenses/`. The three modified typefaces have new family names so they cannot be confused with their sources. Each license stays with its corresponding font. The source files used by the build script are pinned to Google Fonts commit `b5efa9c32e8f9b63005f5cdb1ad5527a77d2cd04`.

To rebuild the three variants and refresh the two open font copies, install `fonttools` and `requests`, then run `python3 tools/build_inspo_fonts.py` from the repository root. The build script downloads from the pinned official Google Fonts repository.

Visual Brain copies a font into its local data folder when you add it with **Add a font…**.

# Fonts

The three typefaces are committed here so that `npm run build` works without an internet connection
(the offline backup plan in `docs/DEMO_SCRIPT.md`). They are loaded with `next/font/local` in
`src/app/layout.tsx`.

| File | Typeface | Weights (variable) | License |
|---|---|---|---|
| `inter-latin-wght-normal.woff2` | Inter | 100-900 | SIL Open Font License 1.1, `LICENSE-Inter-OFL.txt` |
| `space-grotesk-latin-wght-normal.woff2` | Space Grotesk | 300-700 | SIL Open Font License 1.1, `LICENSE-SpaceGrotesk-OFL.txt` |
| `jetbrains-mono-latin-wght-normal.woff2` | JetBrains Mono | 100-800 | SIL Open Font License 1.1, `LICENSE-JetBrainsMono-OFL.txt` |

Source: the Latin, normal-style variable files of the Fontsource packages `@fontsource-variable/inter`,
`@fontsource-variable/space-grotesk` and `@fontsource-variable/jetbrains-mono` (version 5.3.0), which
redistribute the Google Fonts builds unchanged. Only the Latin subset is included, the same subset the
app requested from Google Fonts before.

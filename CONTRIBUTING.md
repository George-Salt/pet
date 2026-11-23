# Contributing to Pet

English and Russian contributions are welcome.

Use the [issue forms](https://github.com/George-Salt/pet/issues/new/choose) for bug reports and feature requests. For a bug, include the Linux distribution, compositor, session type, and steps to reproduce it. For private security reports, see [SECURITY.md](SECURITY.md).

## Develop locally

Install the [system dependencies](README.md#system-dependencies) and run `./launch` from your checkout. If an installed copy is already running, close it with `pet --quit` before launching your checkout.

- `hyprpet/` contains the GTK overlay, audio controls, voice recognition, and local music bridge.
- `pulsesync-addon/` contains the optional Yandex Music integration.

Run the checks that apply to your changes:

```bash
python3 -m compileall -q hyprpet install
for script in launch setup-voice setup-pulsesync; do bash -n "$script"; done
for script in pulsesync-addon/vite.config.js pulsesync-addon/src/*.js; do node --check "$script"; done
```

Verify UI changes in a desktop session. For overlay changes, mention the compositor and test clipping at a screen edge, dragging with the menu open, or fullscreen behavior as relevant. For voice and music changes, include the spoken phrase or terminal command and the playback result.

Open a pull request with a short description, how you verified it, and a screenshot for visible changes. Update both READMEs when the documented controls or installation steps change. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

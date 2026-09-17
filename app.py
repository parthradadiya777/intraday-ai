# Compatibility entrypoint for deployments that still run `python app.py`.
# Always launch the full scanner/server instead of the legacy UI.
# Deployment refresh marker: 2026-09-17.
import runpy

runpy.run_module('server', run_name='__main__')

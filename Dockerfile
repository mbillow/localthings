FROM ghcr.io/home-assistant/home-assistant:stable

# Home Assistant normally installs a custom integration's manifest.json
# requirements itself at integration setup, but that depends on the
# container having outbound network access at exactly that moment and
# repeats the install attempt on every container recreate. Baking
# smartthings-local into the image keeps the dev container usable
# offline and avoids relying on that runtime install path.
RUN pip3 install --no-cache-dir "smartthings-local>=0.1.0"

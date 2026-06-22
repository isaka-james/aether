# Security and privacy

Aether can run things on your computer, so it is built to be careful. Here is what protects
you, what stays private, and what to do before you open it to the internet.

## What protects you

- **It runs on your own machine.** The thinking part runs in a Docker sandbox. Only a small
  helper, the host agent, can touch your desktop, and it only does the actions Aether allows.
- **A login guards everything.** You set a username and password. The password is stored only
  as a secure hash, never in plain text. Sessions use signed tokens.
- **Risky commands ask first.** Anything that could change or delete things, or that needs your
  admin password, waits for you to approve it in the web page. The most dangerous commands
  (like wiping a disk) are blocked outright and never run, even if approved.
- **Your admin password stays hidden.** If a command needs it, it is sent straight to the
  helper and used once. It is never shown in the web page and never written to logs.
- **Strong secrets by default.** The installer generates long random keys for you, so nothing
  ships with a default password.

## What stays private

Almost everything stays on your machine. The only thing that leaves is:

- **The AI request.** Your words go to the AI provider you picked (DeepSeek, OpenAI, or Claude)
  so it can decide what to do. Pick a **local model** and even this stays on your computer. See
  [PROVIDERS.md](PROVIDERS.md).
- **Voice typing (optional).** By default, speech can be sent to Google's free service to turn
  it into text. To keep voice fully on your machine, set `AETHER_STT_PROVIDER=local` in `.env`.

For a fully private setup with nothing leaving your machine, use a local AI model and local
voice typing.

## Before you open it to the internet

On your home network this is already fine. If you want to reach it from outside, do these
first:

- Use a strong password. The installer can make one for you.
- Open it through `ngrok http 8473` (it gives you an https link), or put it behind a reverse
  proxy with https. Https is also what lets the microphone work on phones away from home.
- With ngrok, claim its free static domain so your link stays the same every time. A fixed
  address is easier to use and easier to keep locked down than a random one that changes.
- Add a second lock if you can: a VPN like Tailscale, or basic auth on the proxy.
- Make sure Docker starts on boot, and leave the host agent running as your normal user, not
  as root.

## A note for the curious

Aether gives you a full, safe path to run commands on your machine, which is what makes it so
capable. The checks above keep that power in your hands: one login, an approval step for
anything risky, and a hard block on the truly destructive. Anyone who has your login can
control your desktop, so treat that password like the key to your house.

Found a problem? Open an issue, or send a private report for anything sensitive.

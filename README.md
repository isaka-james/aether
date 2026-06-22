<div align="center">

# ◈ Aether

**Talk to your computer. It listens, and it acts.**

Aether is a self hosted voice assistant for your KDE Linux desktop. Speak or type from your
phone, laptop, or tablet, and it runs things on your machine and talks back. You choose the AI
model behind it, and everything stays on your computer unless you decide otherwise.

It answers in the calm, dry voice of a butler who is impressed by nothing, so even the small
stuff feels like a small luxury.

</div>

---

## What it can do

Just say what you want. A few examples:

- "Lock the screen", or "unlock it" (works just as well from a cabin in Alaska)
- "Play some jazz on YouTube", "turn the video down", "skip this", "stop"
- "Read my notifications"
- "Set the volume to 20", "mute my mic"
- "Dim the screen to 30"
- "Find my budget spreadsheet"
- "How much RAM is free?", "what's my battery?", "am I on wifi?"
- "Open Chrome", "how many windows are open?", "close this tab"
- "What's the weather? Do I need a jacket?"

This is just a taste. Aether is a real agent, not a fixed list of commands. When there is no
ready made action for something, it looks around your machine and runs the right commands
itself, so it can do far more than the examples above. When it is unsure it asks you a quick
question in the web page, and anything risky waits for your approval first. Full list in
[docs/CAPABILITIES.md](docs/CAPABILITIES.md).

## Install

From inside your KDE session, open a terminal and run:

```bash
git clone https://github.com/isaka-james/aether && cd aether
bash scripts/install.sh
```

The installer asks a few simple questions (your login, which AI model, what to set up), then
starts everything and shows you the link to open. It also sets Aether to start every time you
log in, so it is always there. The first run downloads a few things and takes a few minutes.

To remove it later: `bash scripts/uninstall.sh`.

## Use it

Open the link the installer printed and sign in:

- On this computer: `http://localhost:8473` (voice works here)
- Another device on your wifi: `http://your-pc-ip:8473` (typing works here)
- From anywhere: run `ngrok http 8473` and open the `https` link it gives you. This is the easy
  way to reach your PC from outside your home, with no router setup.

Tap the orb and talk, or type in the box.

### Talking by voice on a phone

Phones (especially iPhones and iPads) only allow the microphone on secure `https` pages. So for
voice on a phone, open the **ngrok `https` link**, not the plain `http` address. Typing always
works, even without https, if you would rather just chat.

Tip: in your browser menu, choose **Install** or **Add to Home Screen**. Aether then opens like
a normal app, full screen, with voice and notifications.

If you use ngrok a lot, claim its **free static domain** so your link never changes. A fixed
address is easier to use and easier to keep locked down. See [docs/SECURITY.md](docs/SECURITY.md).

## Pick your AI model

Aether works with DeepSeek (the default), OpenAI, Claude, or a model on your own computer.
A quick guide:

- **Local model**: free and fully private. Our top pick if you have a decent GPU.
- **DeepSeek**: the budget cloud option. Even a heavy user spends about 2 dollars a month, less
  than the coffee you drink while it works.
- **Claude or OpenAI**: the sharpest, and the priciest. For when money is not the question.

Switch any time by editing one line. See [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Privacy and security

Aether runs on your own machine. The only thing that leaves it is the AI request to the
provider you picked, and you can avoid even that by running a local model. Your login protects
everything, the assistant runs in a sandbox, and risky commands always ask before they run. If
you plan to open it to the internet, read [docs/SECURITY.md](docs/SECURITY.md) first.

## What you need

- A KDE Plasma desktop on Linux
- Docker
- Google Chrome (only for the YouTube features)
- An AI key from DeepSeek, OpenAI, or Claude, or a model running locally (no key needed)

---

<div align="center">

Made for people who want a computer that listens, on their own terms.
If you like it, a star helps others find it.

</div>

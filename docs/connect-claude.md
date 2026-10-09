# Connect Claude to Deckwright

This guide is for anyone who wants to build decks with Claude. You do not need to install anything. You do not need to use a terminal.

## No team server? Run Deckwright on your own computer instead

If your company has no Deckwright server, you can run it on your own computer. You need Claude Desktop and Docker Desktop. You do not need a terminal.

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Download `deckwright.mcpb` from the [latest release](https://github.com/digitalchild/deckwright/releases/latest).
3. Double-click the file. Or open Claude Desktop, go to **Settings**, then **Extensions**, then **Install extension**.
4. Pick a folder. The default is `~/Deckwright`.
5. The first start downloads about 1 GB. While it downloads, ask Claude about Deckwright. Claude tells you that the download is running. Wait a few minutes. Then quit Claude completely and open it again. The file `download.log` in your folder shows the progress.

Docker Desktop is free for small companies, education and personal use. Larger companies need a paid plan.

Your folder has three parts:

- `Decks`: your finished decks, diagrams and previews.
- `Templates`: your template packs, one folder each.
- `Inbox`: put a `.pptx` or an image here. Then ask Claude to use it.

Deckwright can see only this folder. To add a template, put the `.pptx` in `Inbox`. Then ask Claude: "Add Inbox/acme.pptx as a template called acme". The container cannot see fonts installed on your computer. Put the font files in `Inbox` too, and tell Claude to use them. To share a template with a teammate, copy its folder from your `Templates` to theirs.

If you use a team server, skip this part and follow the steps below.

## 1. Open Claude

Open the Claude desktop app, or go to [claude.ai](https://claude.ai) in your browser.

## 2. Add the connector

1. Open **Settings**.
2. Go to **Connectors**.
3. Choose **Add custom connector**.
4. For the name, type `Deckwright`.
5. For the URL, paste the address your admin gave you. It looks like this:

   ```
   https://decks.example.com/mcp
   ```

6. Save the connector.

## 3. Sign in

Claude asks you to sign in. Use your work Google account.

Sign in only with the Google account for your company. An account from another company is refused.

The first time, Deckwright asks "Allow Claude to use Deckwright?". The page shows the app name and where you go next. Select **Allow** only if you just started the connection yourself. If you did not, select **Deny**. Deckwright remembers your answer for that app.

## 4. Ask for a deck

Open a new chat. Name a template and describe your deck. Here are two examples:

> Using the "acme" template, build a 5-slide deck about our Q3 results. Include a title slide, an agenda, two stat slides, and a closing slide.

> Using the "acme" template, build a deck that introduces our new product to a customer. Include a title slide, three feature slides, and a closing slide.

Claude builds the deck and replies with a download link.

## What happens with the download link

The link opens in your normal web browser. You do not need to sign in to open it. The file downloads straight away.

The link expires after 24 hours by default. Your admin can change this. After it expires, the link no longer works.

## If something goes wrong

- **"This account is not allowed."** You signed in with the wrong Google account. Sign out, then sign in again with your work account for the right company.
- **The download link does not open.** The link has likely expired. Ask Claude to build the deck again. This gives you a new link.
- **Anything else.** Ask your admin for help. Tell them what you asked Claude to do, and what message you saw.

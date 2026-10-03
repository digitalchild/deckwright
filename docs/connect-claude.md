# Connect Claude to Deckwright

This guide is for anyone who wants to build decks with Claude. You do not need to install anything. You do not need to use a terminal.

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

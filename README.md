# Jack Stonehouse Stock Monitor

A free GitHub Actions monitor for the 15 selected Jack Stonehouse products.

## What it does

- Checks every five minutes.
- Sends a Discord embed when a product changes into stock.
- Uses no paid server, proxy or Python package.
- Remembers previous status in `state.json` to avoid duplicate alerts.
- Retries temporary page failures and avoids treating an unknown page as a restock.

## Setup

1. Create a public GitHub repository or add these files to your existing monitor repository.
2. Add the repository secret `DISCORD_WEBHOOK_URL`.
3. Open **Actions → Jack Stonehouse Monitor → Run workflow**.
4. Tick the test-notification option and run it.
5. Run it again without the test option to establish the initial stock baseline.

The first real run may alert for products already in stock because `state.json` starts empty. To avoid that, temporarily omit the webhook on the first run or accept the one-time baseline alerts.

## Important

The URLs containing `#attribute...` fragments select a browser-side variant. URL fragments are not sent to the server, so the monitor checks the parent product page. For these products, verify during the first run that the page-level stock result matches the required colour/variant.

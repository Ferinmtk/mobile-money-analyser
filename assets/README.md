# assets

Optional brand logos for the dashboard. Drop files here and the app uses them
in place of the built-in fallback icons:

    assets/mpesa.png     (or safaricom.png)
    assets/airtel.png    (or airtel-money.png)
    assets/both.png      (or combined.png)

`.png`, `.jpg`, `.svg` and `.webp` all work. If a file is missing the app falls
back to its own icons, so nothing breaks.

## Why this folder is gitignored

Safaricom, M-Pesa and Airtel logos are registered trademarks. Using them
locally to make your own tool readable is one thing; committing them to a
public repository redistributes someone else's mark and implies an endorsement
that does not exist. So `assets/*` is excluded from version control, and only
this README is tracked.

For a commercial release, get written permission from each operator.

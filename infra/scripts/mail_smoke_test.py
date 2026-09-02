# infra/scripts/mail_smoke_test.py
"""Send one test email via Graph. Usage: uv run python scripts/mail_smoke_test.py you@premiumiq.com"""

import sys

from app.notifications.email_sender import send_email

if __name__ == "__main__":
    to_address = sys.argv[1]
    send_email(
        to=[to_address],
        subject="RWB mail smoke test",
        html_body="<p>If you got this, MAIL_* config and the Graph access policy are working.</p>",
    )
    print(f"Sent to {to_address}")

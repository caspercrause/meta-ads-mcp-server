"""
Operator enrollment helper for the write server's TOTP approval codes.

Run this LOCALLY in a terminal, never inside an AI conversation, so the
secret stays out of transcripts. Nothing is written to disk or sent
anywhere; the QR renders in the terminal for the operator to scan.

Usage:
    python totp_enroll.py                     # new secret, generic label
    python totp_enroll.py --name alice        # new secret labelled 'alice'
    python totp_enroll.py --secret BASE32...  # re-render an existing secret
                                              # (same QR on another phone)

Per-operator mode: run once per teammate with --name, let each scan their
own QR, then put 'name:SECRET' pairs into META_WRITE_TOTP_SECRETS.
Shared mode: run once, let everyone scan the same QR, put the secret into
META_WRITE_TOTP_SECRET.

The QR needs the 'qrcode' package (pip install qrcode); without it the
script prints the otpauth:// URI and the setup key for manual entry
(authenticator apps accept 'Enter a setup key').
"""
import argparse
import os

import pyotp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='operator',
                        help="label shown in the authenticator app")
    parser.add_argument('--secret', default=None,
                        help="existing base32 secret to re-render instead of "
                             "generating a new one")
    parser.add_argument('--from-env', action='store_true',
                        help="re-render META_WRITE_TOTP_SECRET from the local .env")
    args = parser.parse_args()

    secret = args.secret
    if args.from_env:
        from dotenv import load_dotenv
        load_dotenv()
        secret = os.getenv('META_WRITE_TOTP_SECRET')
        if not secret:
            raise SystemExit("META_WRITE_TOTP_SECRET is not set in .env")
    secret = (secret or pyotp.random_base32()).strip().upper()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=args.name, issuer_name='Meta Ads Write')

    print(f"operator:  {args.name}")
    print(f"setup key: {secret}   (manual entry: 'Enter a setup key', time-based)")
    print(f"uri:       {uri}")
    print()
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
        print("Scan with the phone's camera or the authenticator app.")
    except ImportError:
        print("For a scannable QR: pip install qrcode, then re-run with "
              "--secret and the setup key above.")


if __name__ == '__main__':
    main()

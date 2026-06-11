import os

from uploader.youtube_oauth import build_youtube_credentials, export_refresh_token_json


def main():
    os.environ["YOUTUBE_OAUTH_INTERACTIVE"] = "1"
    creds = build_youtube_credentials(interactive=True)
    print("\nStore these values in AWS SSM or your local environment:")
    print(export_refresh_token_json(creds))


if __name__ == "__main__":
    main()

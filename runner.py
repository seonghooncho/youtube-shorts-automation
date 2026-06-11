from shared.jobs.runner import _do_generate, _do_upload


if __name__ == "__main__":
    import os

    mode = os.getenv("MODE", "upload").lower().strip()
    if mode == "generate":
        _do_generate()
    elif mode == "upload":
        _do_upload()
    elif mode in ("both", "all"):
        _do_generate()
        _do_upload()
    else:
        raise SystemExit(f"Unknown MODE: {mode}")

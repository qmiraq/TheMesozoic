def validate_steam_id(steam_id: str):

    if not steam_id.isdigit():
        return False

    if len(steam_id) != 17:
        return False

    if not steam_id.startswith("7656"):
        return False

    return True
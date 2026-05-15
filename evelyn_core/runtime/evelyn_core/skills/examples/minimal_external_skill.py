name = "example_external"
routes = ("example_external",)
sources = ("text", "voice")
description = "Example external skill showing the minimum Evelyn skill contract."


async def execute(context):
    return {
        "skill": name,
        "source": context.source,
        "guild_id": context.guild_id,
        "extras": dict(context.extras),
    }

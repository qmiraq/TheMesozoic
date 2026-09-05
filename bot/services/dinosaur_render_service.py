from __future__ import annotations

import logging

import discord
from discord.ext import commands


LOGGER = logging.getLogger(__name__)
DINOSAUR_RENDER_CHANNEL_ID = 1527443013884973248
DINOSAUR_RENDER_MESSAGE_IDS = {
    "Tenontosaurus": 1529826374087479327,
    "Allosaurus": 1529834567886700544,
    "Austroraptor": 1529989052558872648,
    "Tyrannosaurus": 1529872833881047252,
    "Carnotaurus": 1529878444987650049,
    "Ceratosaurus": 1529883334019256440,
    "Pachycephalosaurus": 1529909265207263353,
    "Herrerasaurus": 1529912389787582484,
    "Troodon": 1529914349433389219,
    "Pteranodon": 1529916556165775511,
    "Dilophosaurus": 1529925969681322135,
    "Omniraptor": 1529934138520371331,
    "Deinosuchus": 1529989194204713011,
    "Dryosaurus": 1529999706585825312,
    "Beipiaosaurus": 1530000355226554400,
    "Triceratops": 1530004315891171428,
    "Kentrosaurus": 1530007983994441738,
    "Maiasaura": 1530011589833658398,
    "Stegosaurus": 1530017088553484469,
    "Diabloceratops": 1530019484843118594,
    "Gallimimus": 1530019948594991235,
    "Hypsilophodon": 1530023464445153281,
}


def _normalized_species(value: str) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


_NORMALIZED_RENDER_SPECIES = {
    _normalized_species(species): species for species in DINOSAUR_RENDER_MESSAGE_IDS
}
_NORMALIZED_RENDER_SPECIES["tyrannosaurusrex"] = "Tyrannosaurus"
_NORMALIZED_RENDER_SPECIES["trex"] = "Tyrannosaurus"


def render_message_id(species: str) -> int | None:
    wanted = _normalized_species(species)
    mapped = _NORMALIZED_RENDER_SPECIES.get(wanted)
    if mapped is None:
        for alias in sorted(_NORMALIZED_RENDER_SPECIES, key=len, reverse=True):
            if alias and alias in wanted:
                mapped = _NORMALIZED_RENDER_SPECIES[alias]
                break
    return DINOSAUR_RENDER_MESSAGE_IDS.get(mapped or "")


async def dinosaur_render_file(
    bot: commands.Bot,
    species: str,
) -> discord.File | None:
    message_id = render_message_id(species)
    if message_id is None:
        return None
    try:
        channel = bot.get_channel(DINOSAUR_RENDER_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(DINOSAUR_RENDER_CHANNEL_ID)
        message = await channel.fetch_message(message_id)
        attachment = next(
            (
                item
                for item in message.attachments
                if str(item.content_type or "").casefold().startswith("image/")
                or str(item.filename or "").casefold().endswith(
                    (".png", ".jpg", ".jpeg", ".webp", ".gif")
                )
            ),
            None,
        )
        if attachment is None:
            LOGGER.warning(
                "Dinosaur render message %s for %s has no image attachment",
                message_id,
                species,
            )
            return None
        original_name = str(attachment.filename or "")
        extension = "." + original_name.rsplit(".", 1)[-1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            extension = ".png"
        safe_species = "".join(character for character in species if character.isalnum())
        return await attachment.to_file(
            filename=f"{safe_species or 'Dinosaur'}-render{extension}",
            use_cached=True,
        )
    except (discord.HTTPException, discord.NotFound, discord.Forbidden):
        LOGGER.exception(
            "Could not load the dinosaur render for %s from message %s",
            species,
            message_id,
        )
        return None

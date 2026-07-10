using Microsoft.AspNetCore.Mvc;
using miniEniac_RCON.Models;
using miniEniac_RCON.Services;

namespace miniEniac_RCON.Controllers;

[ApiController]
[Route("[controller]")]
public class RconController : ControllerBase
{
    private readonly EvrimaRconService _rcon;

    public RconController(EvrimaRconService rcon)
    {
        _rcon = rcon;
    }


    [HttpGet("players")]
    public async Task<IActionResult> Players()
    {
        var result = await _rcon.GetPlayers();

        return Ok(result);
    }
    [HttpGet("server")]
    public async Task<IActionResult> Server()
    {
        ServerDetailsResponse result = await _rcon.GetServerDetails();

        return Ok(result);
    }
    [HttpGet("playerdata")]
    public async Task<IActionResult> PlayerData()
    {
        var players = await _rcon.GetPlayerData();

        return Ok(players);
    }
}
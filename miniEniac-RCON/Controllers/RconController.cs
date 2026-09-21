using Microsoft.AspNetCore.Mvc;
using miniEniac_RCON.Models;
using miniEniac_RCON.Services;
using TheIsleEvrimaRconClient;

namespace miniEniac_RCON.Controllers;

[ApiController]
[Route("[controller]")]
public class RconController : ControllerBase
{
    private readonly EvrimaRconService _rcon;
    private readonly IConfiguration _configuration;

    public RconController(
        EvrimaRconService rcon,
        IConfiguration configuration)
    {
        _rcon = rcon;
        _configuration = configuration;
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

    private bool IsLocalRequest()
    {
        var remoteIp = HttpContext.Connection.RemoteIpAddress;

        if (remoteIp is null)
            return false;

        return System.Net.IPAddress.IsLoopback(remoteIp)
               || remoteIp.ToString() == "127.0.0.1"
               || remoteIp.ToString() == "::1";
    }

    private EvrimaRconClient CreateClient()
    {
        string host = _configuration["EvrimaRcon:Host"]
            ?? throw new InvalidOperationException("EvrimaRcon:Host is not configured.");

        string port = _configuration["EvrimaRcon:Port"]
            ?? throw new InvalidOperationException("EvrimaRcon:Port is not configured.");

        string password = _configuration["EvrimaRcon:Password"]
            ?? throw new InvalidOperationException("EvrimaRcon:Password is not configured.");

        return new EvrimaRconClient(
            new EvrimaRconClientConfiguration
            {
                Host = System.Net.IPAddress.Parse(host),
                Port = int.Parse(port),
                Password = password
            }
        );
    }

    [HttpPost("update-playables")]
    public async Task<IActionResult> UpdatePlayables([FromQuery] string classes)
    {
        if (!IsLocalRequest())
            return Forbid();

        if (string.IsNullOrWhiteSpace(classes))
        {
            return BadRequest(new
            {
                ok = false,
                error = "Playable class list is required."
            });
        }

        string roster = classes.Trim();

        using EvrimaRconClient client = CreateClient();

        if (!await client.ConnectAsync())
        {
            return StatusCode(502, new
            {
                ok = false,
                error = "Could not connect to EVRIMA RCON."
            });
        }

        string response = await client.SendCommandAsync(
            "updateplayables",
            roster
        );

        return Ok(new
        {
            ok = true,
            response
        });
    }


}

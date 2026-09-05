using Microsoft.AspNetCore.Mvc;
using System.Net;
using TheIsleEvrimaRconClient;

namespace miniEniac_RCON.Controllers;

[ApiController]
[Route("Rcon")]
public sealed class AutoRestartController : ControllerBase
{
    private readonly IConfiguration _configuration;

    public AutoRestartController(IConfiguration configuration)
    {
        _configuration = configuration;
    }

    private bool IsLocalRequest()
    {
        IPAddress? address = HttpContext.Connection.RemoteIpAddress;
        return address is not null && IPAddress.IsLoopback(address);
    }

    private EvrimaRconClient CreateClient()
    {
        string host = _configuration["EvrimaRcon:Host"]
            ?? throw new InvalidOperationException("EvrimaRcon host is missing.");
        string port = _configuration["EvrimaRcon:Port"]
            ?? throw new InvalidOperationException("EvrimaRcon port is missing.");
        string password = _configuration["EvrimaRcon:Password"]
            ?? throw new InvalidOperationException("EvrimaRcon password is missing.");

        return new EvrimaRconClient(
            new EvrimaRconClientConfiguration
            {
                Host = IPAddress.Parse(host),
                Port = int.Parse(port),
                Password = password
            }
        );
    }

    [HttpPost("announce")]
    public async Task<IActionResult> Announce(
        [FromBody] RestartAnnouncementRequest request
    )
    {
        if (!IsLocalRequest())
        {
            return Forbid();
        }
        string message = (request.Message ?? string.Empty).Trim();
        if (message.Length is < 1 or > 512)
        {
            return BadRequest(
                new { ok = false, error = "Message must contain 1 to 512 characters." }
            );
        }

        using EvrimaRconClient client = CreateClient();
        if (!await client.ConnectAsync())
        {
            return StatusCode(
                StatusCodes.Status503ServiceUnavailable,
                new { ok = false, error = "Could not connect to Evrima RCON." }
            );
        }
        string response = await client.SendCommandAsync(
            EvrimaRconCommand.Announce,
            message
        );
        return Ok(new { ok = true, response });
    }

    [HttpPost("save")]
    public async Task<IActionResult> Save()
    {
        if (!IsLocalRequest())
        {
            return Forbid();
        }
        using EvrimaRconClient client = CreateClient();
        if (!await client.ConnectAsync())
        {
            return StatusCode(
                StatusCodes.Status503ServiceUnavailable,
                new { ok = false, error = "Could not connect to Evrima RCON." }
            );
        }
        string response = await client.SendCommandAsync(EvrimaRconCommand.Save);
        return Ok(new { ok = true, response });
    }

    [HttpPost("direct-message")]
    public async Task<IActionResult> DirectMessage(
        [FromBody] DirectMessageRequest request
    )
    {
        if (!IsLocalRequest())
        {
            return Forbid();
        }
        string player = (request.Player ?? string.Empty).Trim();
        string message = (request.Message ?? string.Empty).Trim();
        if (player.Length is < 1 or > 128 || player.Contains(','))
        {
            return BadRequest(
                new { ok = false, error = "Player identifier is invalid." }
            );
        }
        if (message.Length is < 1 or > 1024)
        {
            return BadRequest(
                new { ok = false, error = "Message must contain 1 to 1024 characters." }
            );
        }

        using EvrimaRconClient client = CreateClient();
        if (!await client.ConnectAsync())
        {
            return StatusCode(
                StatusCodes.Status503ServiceUnavailable,
                new { ok = false, error = "Could not connect to Evrima RCON." }
            );
        }
        string response = await client.SendCommandAsync(
            EvrimaRconCommand.DirectMessage,
            $"{player},{message}"
        );
        return Ok(new { ok = true, response });
    }
}

public sealed class RestartAnnouncementRequest
{
    public string? Message { get; init; }
}

public sealed class DirectMessageRequest
{
    public string? Player { get; init; }
    public string? Message { get; init; }
}

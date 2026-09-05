using Microsoft.AspNetCore.Mvc;
using System.Net;
using TheIsleEvrimaRconClient;

namespace miniEniac_RCON.Controllers;

[ApiController]
[Route("Rcon")]
public sealed class PopulationControlController : ControllerBase
{
    private readonly IConfiguration _configuration;

    public PopulationControlController(IConfiguration configuration)
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

    [HttpPost("update-playables")]
    public async Task<IActionResult> UpdatePlayables()
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
        string response = await client.SendCommandAsync(
            EvrimaRconCommand.UpdatePlayables
        );
        return Ok(new { ok = true, response });
    }
}

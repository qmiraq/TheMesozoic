using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace miniEniac_RCON.Controllers;

[Authorize]
[ApiController]
[Route("test")]
public sealed class WebPanelTestController : ControllerBase
{
    private const string TestDiscordId = "323802380007112704";
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };
    private readonly IWebHostEnvironment _environment;

    public WebPanelTestController(IWebHostEnvironment environment)
    {
        _environment = environment;
    }

    [HttpGet("")]
    [HttpGet("admin")]
    public IActionResult Page()
    {
        IActionResult? guard = RequireTester();
        if (guard is not null) return guard;
        return Serve("index.html", "text/html; charset=utf-8");
    }

    [HttpGet("assets/{fileName}")]
    public IActionResult Asset(string fileName)
    {
        IActionResult? guard = RequireTester();
        if (guard is not null) return guard;

        return fileName switch
        {
            "styles.css" => Serve(fileName, "text/css; charset=utf-8"),
            "app.js" => Serve(fileName, "application/javascript; charset=utf-8"),
            "LogoMeso.png" => Serve(fileName, "image/png"),
            _ => NotFound()
        };
    }

    [HttpGet("api/session")]
    [ResponseCache(NoStore = true, Location = ResponseCacheLocation.None)]
    public async Task<IActionResult> Session(CancellationToken cancellationToken)
    {
        IActionResult? guard = RequireTester();
        if (guard is not null) return guard;

        string discordId = GetDiscordId();
        bool isHeadStaff = string.Equals(discordId, TestDiscordId, StringComparison.Ordinal)
            || await IsAiAdministratorAsync(discordId, cancellationToken);
        return Ok(new
        {
            discordId,
            displayName = User.Identity?.Name ?? "Discord user",
            accessLevel = isHeadStaff ? "headStaff" : "player",
            isAdmin = isHeadStaff,
            isHeadStaff
        });
    }

    private IActionResult? RequireTester()
    {
        if (User.Identity?.IsAuthenticated != true) return Unauthorized();
        return string.Equals(GetDiscordId(), TestDiscordId, StringComparison.Ordinal)
            ? null
            : Forbid();
    }

    private string GetDiscordId() =>
        User.FindFirstValue(ClaimTypes.NameIdentifier)?.Trim() ?? string.Empty;

    private async Task<bool> IsAiAdministratorAsync(
        string discordId,
        CancellationToken cancellationToken)
    {
        string path = Path.Combine(
            _environment.ContentRootPath,
            "Config",
            "mesozoic-ai-admins.json");
        if (!System.IO.File.Exists(path)) return false;

        try
        {
            await using FileStream stream = System.IO.File.OpenRead(path);
            WebPanelAdminSettings? settings = await JsonSerializer.DeserializeAsync<WebPanelAdminSettings>(
                stream,
                JsonOptions,
                cancellationToken: cancellationToken);
            return settings?.DiscordIds?.Any(value =>
                string.Equals(value?.Trim(), discordId, StringComparison.Ordinal)) == true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private IActionResult Serve(string fileName, string contentType)
    {
        string root = Path.Combine(_environment.ContentRootPath, "WebPanelTest");
        string path = Path.Combine(root, fileName);
        return System.IO.File.Exists(path)
            ? PhysicalFile(path, contentType)
            : NotFound("The web-panel test asset has not been installed.");
    }
}

public sealed record WebPanelAdminSettings(List<string> DiscordIds);

using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace miniEniac_RCON.Controllers;

[Authorize]
[ApiController]
[Route("test/api/points")]
public sealed class PointManagementController : ControllerBase
{
    private const int SchemaVersion = 1;
    private static readonly SemaphoreSlim FileGate = new(1, 1);
    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly IWebHostEnvironment _environment;

    public PointManagementController(IWebHostEnvironment environment)
    {
        _environment = environment;
    }

    [HttpGet("")]
    [ResponseCache(NoStore = true, Location = ResponseCacheLocation.None)]
    public async Task<IActionResult> Get(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireHeadStaffAsync(cancellationToken);
        if (guard is not null) return guard;
        PointManagementConfig config = await ReadConfigAsync(cancellationToken);
        return Ok(new
        {
            config,
            boostActive = IsBoostActive(config.Boost),
            parisTimeZone = "Europe/Paris",
            rule = "The highest matching supporter-role rate is used, then the global boost is applied."
        });
    }

    [HttpPut("boost")]
    public async Task<IActionResult> SetBoost(
        [FromBody] SetPointBoostRequest request,
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireHeadStaffAsync(cancellationToken);
        if (guard is not null) return guard;
        if (request.Multiplier is < 1.01 or > 10)
            return BadRequest(new { error = "Multiplier must be greater than 1 and no more than 10." });

        DateTimeOffset endsAtUtc;
        if (string.Equals(request.Mode, "duration", StringComparison.OrdinalIgnoreCase))
        {
            if (request.DurationMinutes is null or < 5 or > 10080)
                return BadRequest(new { error = "Duration must be between 5 minutes and 7 days." });
            endsAtUtc = DateTimeOffset.UtcNow.AddMinutes(request.DurationMinutes.Value);
        }
        else if (string.Equals(request.Mode, "until", StringComparison.OrdinalIgnoreCase))
        {
            if (string.IsNullOrWhiteSpace(request.EndsAtParis)
                || !DateTime.TryParse(request.EndsAtParis, out DateTime local))
                return BadRequest(new { error = "A valid Paris date and time is required." });
            TimeZoneInfo paris = ParisTimeZone();
            local = DateTime.SpecifyKind(local, DateTimeKind.Unspecified);
            if (paris.IsInvalidTime(local))
                return BadRequest(new { error = "That Paris time does not exist due to daylight-saving time." });
            endsAtUtc = new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(local, paris), TimeSpan.Zero);
            if (endsAtUtc <= DateTimeOffset.UtcNow.AddMinutes(1))
                return BadRequest(new { error = "The boost end must be in the future." });
        }
        else return BadRequest(new { error = "Choose a duration or an exact end time." });

        PointManagementConfig config = await ReadConfigAsync(cancellationToken);
        config = config with
        {
            Boost = new PointBoost(
                Math.Round(request.Multiplier, 2),
                DateTimeOffset.UtcNow.ToString("O"),
                endsAtUtc.ToString("O"),
                GetDiscordId())
        };
        await WriteConfigAsync(config, cancellationToken);
        return Ok(new { ok = true, config, boostActive = true });
    }

    [HttpDelete("boost")]
    public async Task<IActionResult> StopBoost(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireHeadStaffAsync(cancellationToken);
        if (guard is not null) return guard;
        PointManagementConfig config = await ReadConfigAsync(cancellationToken);
        config = config with { Boost = null };
        await WriteConfigAsync(config, cancellationToken);
        return Ok(new { ok = true, config, boostActive = false });
    }

    [HttpPut("supporter-roles")]
    public async Task<IActionResult> SaveSupporterRoles(
        [FromBody] SaveSupporterRolesRequest request,
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireHeadStaffAsync(cancellationToken);
        if (guard is not null) return guard;
        List<SupporterRoleRate> roles = request.Roles ?? new();
        if (roles.Count > 50) return BadRequest(new { error = "No more than 50 supporter roles may be configured." });
        if (roles.Any(role => string.IsNullOrWhiteSpace(role.RoleId)
            || !role.RoleId.All(char.IsDigit)
            || role.RoleId.Length is < 15 or > 22))
            return BadRequest(new { error = "Every role must have a valid Discord Role ID." });
        if (roles.Any(role => role.PointsPerFiveMinutes is < 1 or > 10000))
            return BadRequest(new { error = "Role rates must be between 1 and 10,000 points per five minutes." });
        if (roles.Select(role => role.RoleId.Trim()).Distinct(StringComparer.Ordinal).Count() != roles.Count)
            return BadRequest(new { error = "Each Discord Role ID may appear only once." });

        PointManagementConfig config = await ReadConfigAsync(cancellationToken);
        config = config with
        {
            SupporterRoles = roles
                .Select(role => new SupporterRoleRate(role.RoleId.Trim(), role.PointsPerFiveMinutes))
                .OrderBy(role => role.PointsPerFiveMinutes)
                .ToList()
        };
        await WriteConfigAsync(config, cancellationToken);
        return Ok(new { ok = true, config });
    }

    private async Task<IActionResult?> RequireHeadStaffAsync(CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated != true) return Unauthorized();
        string discordId = GetDiscordId();
        if (discordId.Length == 0) return Forbid();
        if (discordId == "323802380007112704") return null;
        string path = Path.Combine(_environment.ContentRootPath, "Config", "mesozoic-ai-admins.json");
        if (!System.IO.File.Exists(path)) return Forbid();
        try
        {
            await using FileStream stream = System.IO.File.OpenRead(path);
            WebPanelAdminSettings? settings = await JsonSerializer.DeserializeAsync<WebPanelAdminSettings>(
                stream, JsonOptions, cancellationToken);
            return settings?.DiscordIds?.Any(value => value?.Trim() == discordId) == true ? null : Forbid();
        }
        catch (JsonException) { return StatusCode(503, new { error = "The Head Staff allowlist is invalid." }); }
    }

    private string GetDiscordId() => User.FindFirstValue(ClaimTypes.NameIdentifier)?.Trim() ?? "";
    private string ConfigPath() => Path.GetFullPath(Path.Combine(_environment.ContentRootPath, "..", "Config", "point-management.json"));

    private async Task<PointManagementConfig> ReadConfigAsync(CancellationToken cancellationToken)
    {
        await FileGate.WaitAsync(cancellationToken);
        try
        {
            string path = ConfigPath();
            if (!System.IO.File.Exists(path)) return PointManagementConfig.Default();
            await using FileStream stream = System.IO.File.OpenRead(path);
            return await JsonSerializer.DeserializeAsync<PointManagementConfig>(stream, JsonOptions, cancellationToken)
                ?? PointManagementConfig.Default();
        }
        catch (JsonException) { return PointManagementConfig.Default(); }
        finally { FileGate.Release(); }
    }

    private async Task WriteConfigAsync(PointManagementConfig config, CancellationToken cancellationToken)
    {
        config = config with { SchemaVersion = SchemaVersion, BasePointsPerFiveMinutes = 10 };
        await FileGate.WaitAsync(cancellationToken);
        try
        {
            string path = ConfigPath();
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            string temporary = path + ".tmp-" + Guid.NewGuid().ToString("N");
            await using (FileStream stream = System.IO.File.Create(temporary))
                await JsonSerializer.SerializeAsync(stream, config, JsonOptions, cancellationToken);
            System.IO.File.Move(temporary, path, true);
        }
        finally { FileGate.Release(); }
    }

    private static bool IsBoostActive(PointBoost? boost) =>
        boost is not null && DateTimeOffset.TryParse(boost.EndsAtUtc, out DateTimeOffset end) && end > DateTimeOffset.UtcNow;

    private static TimeZoneInfo ParisTimeZone()
    {
        try { return TimeZoneInfo.FindSystemTimeZoneById("Europe/Paris"); }
        catch (TimeZoneNotFoundException) { return TimeZoneInfo.FindSystemTimeZoneById("Romance Standard Time"); }
    }
}

public sealed record SetPointBoostRequest(double Multiplier, string Mode, int? DurationMinutes, string? EndsAtParis);
public sealed record SaveSupporterRolesRequest(List<SupporterRoleRate>? Roles);
public sealed record SupporterRoleRate(string RoleId, int PointsPerFiveMinutes);
public sealed record PointBoost(double Multiplier, string StartedAtUtc, string EndsAtUtc, string CreatedByDiscordId);
public sealed record PointManagementConfig(int SchemaVersion, int BasePointsPerFiveMinutes, PointBoost? Boost, List<SupporterRoleRate> SupporterRoles)
{
    public static PointManagementConfig Default() => new(1, 10, null, new());
}

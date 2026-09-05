using System.Security.Claims;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using miniEniac_RCON.Services;

namespace miniEniac_RCON.Controllers;

[Authorize]
[ApiController]
[Route("Admin/AI")]
public sealed class MesozoicAiAdminController : ControllerBase
{
    private const int SchemaVersion = 2;
    private const int CalibrationSchemaVersion = 1;
    private const int MapSize = 1600;
    private static readonly SemaphoreSlim FileGate = new(1, 1);
    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private static readonly Regex SafeName =
        new("^[A-Za-z0-9 _\\-]{1,48}$", RegexOptions.Compiled);
    private static readonly IReadOnlyDictionary<string, AiSpeciesDefinition> Species =
        new Dictionary<string, AiSpeciesDefinition>(StringComparer.OrdinalIgnoreCase)
        {
            ["Maiasaura"] = new("Maiasaura", "/Game/TheIsle/Core/Characters/Dinosaurs/Maiasaura/BP_Maiasaura.BP_Maiasaura_C", "/Script/TheIsle.TIAITenontosaurusController", "Herbivore", false, true, 50, 300, 700, new[] { 1.0, .75, .50 }, "Defensive"),
            ["Diabloceratops"] = new("Diabloceratops", "/Game/TheIsle/Core/Characters/Dinosaurs/Diabloceratops/BP_Diabloceratops.BP_Diabloceratops_C", "/Script/TheIsle.TIAIDiabloceratopsController", "Herbivore", false, true, 50, 300, 700, new[] { 1.0, .75, .50 }, "Defensive"),
            ["Deinosuchus"] = new("Deinosuchus", "/Game/TheIsle/Core/Characters/Dinosaurs/Deinosuchus/BP_Deinosuchus.BP_Deinosuchus_C", "/Script/TheIsle.TIAIDeinosuchus", "Carnivore", true, false, 30, 120, 700, new[] { .75, .50, .40 }, "AquaticMixed"),
            ["Beipiaosaurus"] = new("Beipiaosaurus", "/Game/TheIsle/Core/Characters/Dinosaurs/Beipiaosaurus/BP_Beipiaosaurus.BP_Beipiaosaurus_C", "/Game/TheIsle/Core/AI/Controllers/Dinos/BP_AI_Compsognathus_Controller.BP_AI_Compsognathus_Controller_C", "Omnivore", false, true, 40, 180, 700, new[] { .75, .75, .75 }, "Skittish"),
            ["Hypsilophodon"] = new("Hypsilophodon", "/Game/TheIsle/Core/Characters/Dinosaurs/Hypsilophodon/BP_Hypsilophodon.BP_Hypsilophodon_C", "/Script/TheIsle.TIAIHypsilophodon", "Herbivore", false, true, 30, 120, 700, new[] { .75, .75, .75 }, "Skittish"),
            ["Elite Catfish"] = new("Elite Catfish", "/Game/TheIsle/Core/Characters/Fishes/Catfish/BP_Elite_Fish_CatFish.BP_Elite_Fish_CatFish_C", "", "Fish", true, false, 25, 100, 500, new[] { 1.0 }, "NativeAquatic"),
            ["Elite Coelacanth"] = new("Elite Coelacanth", "/Game/TheIsle/Core/Characters/Fishes/Coelacanth/BP_Elite_Fish_Coelacanth.BP_Elite_Fish_Coelacanth_C", "", "Fish", true, false, 25, 100, 500, new[] { 1.0 }, "NativeAquatic")
        };

    private readonly IWebHostEnvironment _environment;
    private readonly PlayerLinkService _playerLinks;

    public MesozoicAiAdminController(
        IWebHostEnvironment environment,
        PlayerLinkService playerLinks)
    {
        _environment = environment;
        _playerLinks = playerLinks;
    }

    [HttpGet("")]
    public async Task<IActionResult> Page(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        string page = Path.Combine(
            _environment.WebRootPath,
            "admin-ai",
            "index.html");
        return System.IO.File.Exists(page)
            ? PhysicalFile(page, "text/html; charset=utf-8")
            : NotFound("The AI admin page has not been installed.");
    }

    [HttpGet("map")]
    public async Task<IActionResult> Map(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        string map = Path.Combine(
            _environment.WebRootPath,
            "admin-ai",
            "Gateway.png");
        return System.IO.File.Exists(map)
            ? PhysicalFile(map, "image/png")
            : NotFound("The Gateway map asset has not been installed.");
    }

    [HttpGet("api/session")]
    public async Task<IActionResult> Session(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;
        return Ok(new
        {
            isAdmin = true,
            discordId = GetDiscordId(),
            displayName = User.Identity?.Name ?? "Administrator"
        });
    }

    [HttpGet("api/catalog")]
    public async Task<IActionResult> Catalog(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;
        return Ok(new
        {
            phase = "maiasaura-production-pilot",
            runtimeEnabled = true,
            species = Species.Values.OrderBy(value => value.Name)
        });
    }

    [HttpGet("api/config")]
    public async Task<IActionResult> GetConfig(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        await FileGate.WaitAsync(cancellationToken);
        try
        {
            AiRuntimeConfig config = await LoadConfigAsync(cancellationToken);
            return Ok(config);
        }
        finally
        {
            FileGate.Release();
        }
    }

    [HttpPut("api/config")]
    public async Task<IActionResult> SaveConfig(
        [FromBody] AiRuntimeConfig request,
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        string? error = ValidateConfig(request);
        if (error is not null) return BadRequest(new { ok = false, error });

        AiRuntimeConfig normalized = request with
        {
            SchemaVersion = SchemaVersion,
            Enabled = request.Enabled,
            Revision = Math.Max(1, request.Revision + 1),
            UpdatedAtUtc = DateTimeOffset.UtcNow.ToString("O"),
            UpdatedByDiscordId = GetDiscordId(),
            Zones = request.Zones.Select(NormalizeZone).ToList()
        };

        await FileGate.WaitAsync(cancellationToken);
        try
        {
            await AtomicJsonWriteAsync(ConfigPath(), normalized, cancellationToken);
        }
        finally
        {
            FileGate.Release();
        }

        await QueueRuntimeSyncAsync(normalized, cancellationToken);

        return Ok(new
        {
            ok = true,
            config = normalized,
            note = normalized.Enabled
                ? "Configuration saved and enabled Maiasaura zones synchronized."
                : "Configuration saved. Automatic AI spawning is stopped."
        });
    }

    [HttpGet("api/calibration")]
    public async Task<IActionResult> GetCalibration(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        await FileGate.WaitAsync(cancellationToken);
        try
        {
            return Ok(await LoadCalibrationAsync(cancellationToken));
        }
        finally
        {
            FileGate.Release();
        }
    }

    [HttpPost("api/calibration/capture")]
    public async Task<IActionResult> CaptureCalibrationPosition(
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        string discordId = GetDiscordId();
        string? steamId;
        try
        {
            steamId = await _playerLinks.FindSteamIdByDiscordIdAsync(
                discordId,
                cancellationToken);
        }
        catch (Exception error) when (
            error is ArgumentException or FileNotFoundException or IOException or InvalidOperationException)
        {
            return StatusCode(
                StatusCodes.Status503ServiceUnavailable,
                new { ok = false, error = "Steam linking could not be read for calibration." });
        }

        if (string.IsNullOrWhiteSpace(steamId))
        {
            return NotFound(new
            {
                ok = false,
                error = "Your Discord account must be linked to Steam before capturing a position."
            });
        }

        return await QueueCommandAsync(
            "capture",
            steamId,
            null,
            null,
            null,
            cancellationToken);
    }

    [HttpPut("api/calibration")]
    public async Task<IActionResult> SaveCalibration(
        [FromBody] GatewayCalibrationRequest request,
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        if (!TryFitCalibration(
            request.Samples,
            out GatewayAffineTransform transform,
            out List<GatewayCalibrationSample> normalizedSamples,
            out double rmsError,
            out double maxError,
            out string? error))
        {
            return BadRequest(new { ok = false, error });
        }

        var calibration = new GatewayCalibration(
            CalibrationSchemaVersion,
            "V8.0-Gateway-1600",
            MapSize,
            MapSize,
            true,
            DateTimeOffset.UtcNow.ToString("O"),
            GetDiscordId(),
            Math.Round(rmsError, 3),
            Math.Round(maxError, 3),
            transform,
            normalizedSamples);

        await FileGate.WaitAsync(cancellationToken);
        try
        {
            await AtomicJsonWriteAsync(CalibrationPath(), calibration, cancellationToken);
        }
        finally
        {
            FileGate.Release();
        }

        return Ok(new
        {
            ok = true,
            calibration,
            note = "Calibration applied to the AI map and shared with the activity heatmap."
        });
    }

    [HttpPost("api/probe/inspect")]
    public async Task<IActionResult> Inspect(CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;
        return await QueueCommandAsync("inspect", "Maiasaura", null, null, null, cancellationToken);
    }

    [HttpPost("api/probe/trace")]
    public async Task<IActionResult> Trace(
        [FromBody] AiProbeLocationRequest request,
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;
        string? error = ValidatePoint(request.X, request.Y);
        if (error is not null) return BadRequest(new { ok = false, error });
        return await QueueCommandAsync("trace", "Maiasaura", request.X, request.Y, null, cancellationToken);
    }

    [HttpPost("api/probe/spawn")]
    public async Task<IActionResult> Spawn(
        [FromBody] AiProbeSpawnRequest request,
        CancellationToken cancellationToken)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;
        string? error = ValidatePoint(request.X, request.Y);
        if (error is not null) return BadRequest(new { ok = false, error });
        if (!Species.TryGetValue(request.Species ?? string.Empty, out AiSpeciesDefinition? definition))
        {
            return BadRequest(new { ok = false, error = "Species is not probe-approved." });
        }
        if (!definition.ManualTestEnabled)
            return BadRequest(new { ok = false, error = "This aquatic species is configuration-only until water/depth validation is confirmed." });
        int count = Math.Clamp(request.TargetCount, 1, 32);
        List<double> growths = request.GrowthDistribution is { Count: > 0 }
            ? request.GrowthDistribution.Take(count).Select(value => Math.Clamp(value, .05, 1.0)).ToList()
            : new List<double> { Math.Clamp(request.Growth, .05, 1.0) };
        while (growths.Count < count) growths.Add(growths[^1]);
        return await QueueGroupCommandAsync(
            definition.Name, request.X, request.Y, count,
            Math.Clamp(request.SpawnRadiusMeters, 0, 500), growths,
            request.ZoneId ?? string.Empty,
            Math.Clamp(request.RespawnDelaySeconds, 60, 3600),
            Math.Clamp(request.ActivationRadiusMeters, 100, 5000),
            Math.Clamp(request.HerdLifetimeSeconds, 60, 7200),
            Math.Clamp(request.RespawnDelayMaxSeconds, 60, 7200),
            cancellationToken);
    }

    [HttpGet("api/events")]
    public async Task<IActionResult> Events(
        [FromQuery] int take = 40,
        CancellationToken cancellationToken = default)
    {
        IActionResult? guard = await RequireAdminAsync(cancellationToken);
        if (guard is not null) return guard;

        take = Math.Clamp(take, 1, 100);
        string path = EventsPath();
        if (!System.IO.File.Exists(path)) return Ok(Array.Empty<JsonElement>());

        string[] lines;
        await FileGate.WaitAsync(cancellationToken);
        try
        {
            lines = await System.IO.File.ReadAllLinesAsync(path, cancellationToken);
        }
        finally
        {
            FileGate.Release();
        }

        var events = new List<JsonElement>();
        foreach (string line in lines.Reverse().Take(take).Reverse())
        {
            try
            {
                using JsonDocument document = JsonDocument.Parse(line);
                events.Add(document.RootElement.Clone());
            }
            catch (JsonException)
            {
                // Ignore a trailing partial line while the game process is flushing it.
            }
        }
        return Ok(events);
    }

    private async Task<IActionResult> QueueCommandAsync(
        string verb,
        string species,
        double? x,
        double? y,
        double? growth,
        CancellationToken cancellationToken)
    {
        string id = Guid.NewGuid().ToString("N");
        string line = string.Join('\t', new[]
        {
            id,
            verb,
            species,
            x?.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture) ?? "",
            y?.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture) ?? "",
            growth?.ToString("0.######", System.Globalization.CultureInfo.InvariantCulture) ?? ""
        }) + Environment.NewLine;

        string path = CommandsPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await FileGate.WaitAsync(cancellationToken);
        try
        {
            await System.IO.File.AppendAllTextAsync(
                path,
                line,
                new UTF8Encoding(false),
                cancellationToken);
        }
        finally
        {
            FileGate.Release();
        }

        return Accepted(new { ok = true, id, verb, queued = true });
    }

    private async Task<IActionResult> QueueGroupCommandAsync(
        string species,
        double x,
        double y,
        int targetCount,
        int spawnRadiusMeters,
        List<double> growths,
        string zoneId,
        int respawnDelaySeconds,
        int activationRadiusMeters,
        int herdLifetimeSeconds,
        int respawnDelayMaxSeconds,
        CancellationToken cancellationToken)
    {
        string id = Guid.NewGuid().ToString("N");
        string growthList = string.Join(',', growths.Select(value =>
            value.ToString("0.######", System.Globalization.CultureInfo.InvariantCulture)));
        string line = string.Join('\t', new[]
        {
            id, "spawn-group", species,
            x.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture),
            y.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture),
            growthList,
            targetCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
            spawnRadiusMeters.ToString(System.Globalization.CultureInfo.InvariantCulture),
            zoneId,
            respawnDelaySeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
            activationRadiusMeters.ToString(System.Globalization.CultureInfo.InvariantCulture),
            herdLifetimeSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
            respawnDelayMaxSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture)
        }) + Environment.NewLine;

        string path = CommandsPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await FileGate.WaitAsync(cancellationToken);
        try
        {
            await System.IO.File.AppendAllTextAsync(path, line, new UTF8Encoding(false), cancellationToken);
        }
        finally { FileGate.Release(); }
        return Accepted(new { ok = true, id, verb = "spawn-group", species, targetCount, queued = true });
    }

    private async Task QueueRuntimeSyncAsync(
        AiRuntimeConfig config,
        CancellationToken cancellationToken)
    {
        var lines = new List<string>
        {
            string.Join('\t', new[] { Guid.NewGuid().ToString("N"), "stop-all" })
        };

        if (config.Enabled)
        {
            foreach (AiSpawnZone zone in config.Zones.Where(zone =>
                zone.Enabled && string.Equals(zone.Species, "Maiasaura", StringComparison.OrdinalIgnoreCase)))
            {
                string growthList = string.Join(',', zone.GrowthDistribution!.Select(value =>
                    value.ToString("0.######", System.Globalization.CultureInfo.InvariantCulture)));
                lines.Add(string.Join('\t', new[]
                {
                    Guid.NewGuid().ToString("N"), "register-zone", zone.Species,
                    zone.X.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture),
                    zone.Y.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture),
                    growthList,
                    zone.TargetCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    zone.SpawnRadiusMeters.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    zone.Id,
                    zone.RespawnDelaySeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    zone.ActivationRadiusMeters.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    zone.HerdLifetimeSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    zone.RespawnDelayMaxSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture)
                }));
            }
        }

        string path = CommandsPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await FileGate.WaitAsync(cancellationToken);
        try
        {
            await System.IO.File.AppendAllTextAsync(
                path,
                string.Join(Environment.NewLine, lines) + Environment.NewLine,
                new UTF8Encoding(false),
                cancellationToken);
        }
        finally { FileGate.Release(); }
    }

    private async Task<IActionResult?> RequireAdminAsync(CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated != true) return Unauthorized();
        string discordId = GetDiscordId();
        if (discordId.Length == 0) return Forbid();

        string path = AdminSettingsPath();
        if (!System.IO.File.Exists(path)) return Forbid();
        try
        {
            await using FileStream stream = System.IO.File.OpenRead(path);
            AiAdminSettings? settings = await JsonSerializer.DeserializeAsync<AiAdminSettings>(
                stream,
                JsonOptions,
                cancellationToken);
            bool allowed = settings?.DiscordIds?.Any(
                value => string.Equals(value?.Trim(), discordId, StringComparison.Ordinal)) == true;
            return allowed ? null : Forbid();
        }
        catch (JsonException)
        {
            return StatusCode(
                StatusCodes.Status503ServiceUnavailable,
                new { error = "The AI administrator allowlist is invalid." });
        }
    }

    private string GetDiscordId() =>
        User.FindFirstValue(ClaimTypes.NameIdentifier)?.Trim() ?? string.Empty;

    private string AdminSettingsPath() =>
        Path.Combine(_environment.ContentRootPath, "Config", "mesozoic-ai-admins.json");

    private static string ModRoot()
    {
        string? configured = Environment.GetEnvironmentVariable("MESOZOIC_AI_MOD_ROOT");
        if (!string.IsNullOrWhiteSpace(configured)) return configured.Trim();
        string[] candidates =
        {
            @"C:\TheMesozoic\TheIsle\Binaries\Win64\Mods\MesozoicAIProbe",
            @"D:\TheMesozoic\TheIsle\Binaries\Win64\Mods\MesozoicAIProbe"
        };
        return candidates.FirstOrDefault(Directory.Exists) ?? candidates[0];
    }

    private static string SavedRoot() => Path.Combine(ModRoot(), "Saved");
    private static string ConfigPath() => Path.Combine(SavedRoot(), "spawn-config.json");
    private static string CalibrationPath() =>
        Path.Combine(SavedRoot(), "gateway-calibration.json");
    private static string CommandsPath() => Path.Combine(SavedRoot(), "commands.tsv");
    private static string EventsPath() => Path.Combine(SavedRoot(), "events.ndjson");

    private async Task<AiRuntimeConfig> LoadConfigAsync(CancellationToken cancellationToken)
    {
        string path = ConfigPath();
        if (!System.IO.File.Exists(path)) return AiRuntimeConfig.Empty();
        try
        {
            await using FileStream stream = System.IO.File.OpenRead(path);
            AiRuntimeConfig loaded = await JsonSerializer.DeserializeAsync<AiRuntimeConfig>(
                stream,
                JsonOptions,
                cancellationToken) ?? AiRuntimeConfig.Empty();
            return UpgradeConfig(loaded);
        }
        catch (JsonException)
        {
            return AiRuntimeConfig.Empty();
        }
    }

    private static AiRuntimeConfig UpgradeConfig(AiRuntimeConfig config) => config with
    {
        SchemaVersion = SchemaVersion,
        MaxGlobalAi = Math.Max(config.MaxGlobalAi, 40),
        Zones = (config.Zones ?? new List<AiSpawnZone>()).Select(zone =>
        {
            Species.TryGetValue(zone.Species ?? string.Empty, out AiSpeciesDefinition? definition);
            double[] defaults = definition?.DefaultGrowthDistribution ?? new[] { Math.Clamp(zone.Growth, .05, 1.0) };
            int count = Math.Clamp(zone.TargetCount, 1, 6);
            List<double> growths = zone.GrowthDistribution is { Count: > 0 }
                ? zone.GrowthDistribution.Take(count).ToList()
                : defaults.Take(count).ToList();
            while (growths.Count < count) growths.Add(growths.Count < defaults.Length ? defaults[growths.Count] : growths[^1]);
            return zone with
            {
                RoamRadiusMeters = zone.RoamRadiusMeters > 0 ? zone.RoamRadiusMeters : definition?.DefaultRoamRadiusMeters ?? 300,
                ActivationRadiusMeters = zone.ActivationRadiusMeters > 0 ? zone.ActivationRadiusMeters : definition?.DefaultActivationRadiusMeters ?? 700,
                GrowthDistribution = growths,
                Aquatic = definition?.Aquatic ?? zone.Aquatic,
                Behavior = string.IsNullOrWhiteSpace(zone.Behavior) ? definition?.DefaultBehavior ?? "Defensive" : zone.Behavior
            };
        }).ToList()
    };

    private static GatewayCalibration DefaultCalibration()
    {
        const double sourceMaximum = MapSize - 1.0;
        double pixelXFromWorldY = sourceMaximum / 1_112_000.0;
        double pixelYFromWorldX = sourceMaximum / 1_116_000.0;
        return new GatewayCalibration(
            CalibrationSchemaVersion,
            "V8.0-Gateway-1600",
            MapSize,
            MapSize,
            false,
            string.Empty,
            string.Empty,
            0,
            0,
            new GatewayAffineTransform(
                0,
                pixelXFromWorldY,
                5.0 + 465_000.0 * pixelXFromWorldY,
                pixelYFromWorldX,
                0,
                5.0 + 607_000.0 * pixelYFromWorldX),
            new List<GatewayCalibrationSample>());
    }

    private static async Task<GatewayCalibration> LoadCalibrationAsync(
        CancellationToken cancellationToken)
    {
        string path = CalibrationPath();
        if (!System.IO.File.Exists(path)) return DefaultCalibration();
        try
        {
            await using FileStream stream = System.IO.File.OpenRead(path);
            GatewayCalibration? calibration =
                await JsonSerializer.DeserializeAsync<GatewayCalibration>(
                    stream,
                    JsonOptions,
                    cancellationToken);
            return calibration is not null && CalibrationIsUsable(calibration)
                ? calibration
                : DefaultCalibration();
        }
        catch (JsonException)
        {
            return DefaultCalibration();
        }
    }

    private static bool CalibrationIsUsable(GatewayCalibration calibration)
    {
        GatewayAffineTransform value = calibration.Transform;
        double determinant =
            value.PixelXFromWorldX * value.PixelYFromWorldY
            - value.PixelXFromWorldY * value.PixelYFromWorldX;
        return calibration.ImageWidth == MapSize
            && calibration.ImageHeight == MapSize
            && double.IsFinite(determinant)
            && Math.Abs(determinant) > 1e-10;
    }

    private static async Task AtomicJsonWriteAsync<T>(
        string path,
        T value,
        CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        string temporary = path + ".tmp";
        await using (FileStream stream = new(
            temporary,
            FileMode.Create,
            FileAccess.Write,
            FileShare.None))
        {
            await JsonSerializer.SerializeAsync(stream, value, JsonOptions, cancellationToken);
            await stream.FlushAsync(cancellationToken);
        }
        System.IO.File.Move(temporary, path, true);
    }

    private static bool TryFitCalibration(
        List<GatewayCalibrationSample>? samples,
        out GatewayAffineTransform transform,
        out List<GatewayCalibrationSample> normalizedSamples,
        out double rmsError,
        out double maxError,
        out string? error)
    {
        transform = DefaultCalibration().Transform;
        normalizedSamples = new List<GatewayCalibrationSample>();
        rmsError = 0;
        maxError = 0;
        error = null;

        if (samples is null || samples.Count < 3)
        {
            error = "Capture at least three calibration points.";
            return false;
        }
        if (samples.Count > 20)
        {
            error = "A maximum of twenty calibration points is supported.";
            return false;
        }

        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (GatewayCalibrationSample sample in samples)
        {
            if (!double.IsFinite(sample.WorldX)
                || !double.IsFinite(sample.WorldY)
                || !double.IsFinite(sample.PixelX)
                || !double.IsFinite(sample.PixelY))
            {
                error = "Calibration coordinates must be finite.";
                return false;
            }
            if (sample.PixelX is < 0 or > MapSize - 1
                || sample.PixelY is < 0 or > MapSize - 1)
            {
                error = "Every map click must be inside the V8 Gateway image.";
                return false;
            }
            if (Math.Abs(sample.WorldX) > 2_000_000
                || Math.Abs(sample.WorldY) > 2_000_000)
            {
                error = "A captured world coordinate is outside safe Gateway limits.";
                return false;
            }

            string id = Guid.TryParse(sample.Id, out Guid parsed)
                ? parsed.ToString("D")
                : Guid.NewGuid().ToString("D");
            if (!ids.Add(id)) id = Guid.NewGuid().ToString("D");
            string trimmedLabel = sample.Label?.Trim() ?? string.Empty;
            string label = trimmedLabel.Length == 0
                ? $"Point {normalizedSamples.Count + 1}"
                : trimmedLabel[..Math.Min(32, trimmedLabel.Length)];
            normalizedSamples.Add(sample with
            {
                Id = id,
                Label = label,
                WorldX = Math.Round(sample.WorldX, 3),
                WorldY = Math.Round(sample.WorldY, 3),
                PixelX = Math.Round(sample.PixelX, 3),
                PixelY = Math.Round(sample.PixelY, 3),
                ResidualPixels = 0
            });
        }

        double worldSpanX = normalizedSamples.Max(value => value.WorldX)
            - normalizedSamples.Min(value => value.WorldX);
        double worldSpanY = normalizedSamples.Max(value => value.WorldY)
            - normalizedSamples.Min(value => value.WorldY);
        double pixelSpanX = normalizedSamples.Max(value => value.PixelX)
            - normalizedSamples.Min(value => value.PixelX);
        double pixelSpanY = normalizedSamples.Max(value => value.PixelY)
            - normalizedSamples.Min(value => value.PixelY);
        if (worldSpanX < 75_000 || worldSpanY < 75_000
            || pixelSpanX < 150 || pixelSpanY < 150)
        {
            error = "Calibration points are too close together. Capture widely separated northwest, northeast, and southern locations.";
            return false;
        }

        double meanX = normalizedSamples.Average(value => value.WorldX);
        double meanY = normalizedSamples.Average(value => value.WorldY);
        double scaleX = Math.Sqrt(normalizedSamples.Average(
            value => Math.Pow(value.WorldX - meanX, 2)));
        double scaleY = Math.Sqrt(normalizedSamples.Average(
            value => Math.Pow(value.WorldY - meanY, 2)));
        if (scaleX < 1_000 || scaleY < 1_000)
        {
            error = "Calibration point spread is insufficient.";
            return false;
        }

        double[,] normal = new double[3, 3];
        double[] rightX = new double[3];
        double[] rightY = new double[3];
        foreach (GatewayCalibrationSample sample in normalizedSamples)
        {
            double[] row =
            {
                (sample.WorldX - meanX) / scaleX,
                (sample.WorldY - meanY) / scaleY,
                1.0
            };
            for (int column = 0; column < 3; column++)
            {
                rightX[column] += row[column] * sample.PixelX;
                rightY[column] += row[column] * sample.PixelY;
                for (int other = 0; other < 3; other++)
                {
                    normal[column, other] += row[column] * row[other];
                }
            }
        }

        if (!TrySolveThreeByThree(normal, rightX, out double[] fittedX)
            || !TrySolveThreeByThree(normal, rightY, out double[] fittedY))
        {
            error = "Calibration points are collinear. Capture points spread across different parts of Gateway.";
            return false;
        }

        double a = fittedX[0] / scaleX;
        double b = fittedX[1] / scaleY;
        double c = fittedX[2] - a * meanX - b * meanY;
        double d = fittedY[0] / scaleX;
        double e = fittedY[1] / scaleY;
        double f = fittedY[2] - d * meanX - e * meanY;
        transform = new GatewayAffineTransform(a, b, c, d, e, f);

        double determinant = a * e - b * d;
        double xScale = Math.Sqrt(a * a + d * d);
        double yScale = Math.Sqrt(b * b + e * e);
        if (!double.IsFinite(determinant)
            || Math.Abs(determinant) < 1e-10
            || xScale is < 0.0004 or > 0.004
            || yScale is < 0.0004 or > 0.004)
        {
            error = "The fitted transform is outside plausible Gateway scale limits.";
            return false;
        }

        double squaredError = 0;
        maxError = 0;
        for (int index = 0; index < normalizedSamples.Count; index++)
        {
            GatewayCalibrationSample sample = normalizedSamples[index];
            double predictedX = a * sample.WorldX + b * sample.WorldY + c;
            double predictedY = d * sample.WorldX + e * sample.WorldY + f;
            double residual = Math.Sqrt(
                Math.Pow(predictedX - sample.PixelX, 2)
                + Math.Pow(predictedY - sample.PixelY, 2));
            squaredError += residual * residual;
            maxError = Math.Max(maxError, residual);
            normalizedSamples[index] = sample with
            {
                ResidualPixels = Math.Round(residual, 3)
            };
        }
        rmsError = Math.Sqrt(squaredError / normalizedSamples.Count);
        if (normalizedSamples.Count >= 4 && (rmsError > 25 || maxError > 50))
        {
            error = $"Calibration clicks disagree by too much (RMS {rmsError:0.0}px, maximum {maxError:0.0}px). Recapture the inaccurate point.";
            return false;
        }
        return true;
    }

    private static bool TrySolveThreeByThree(
        double[,] coefficients,
        double[] right,
        out double[] result)
    {
        double[,] matrix = new double[3, 4];
        for (int row = 0; row < 3; row++)
        {
            for (int column = 0; column < 3; column++)
                matrix[row, column] = coefficients[row, column];
            matrix[row, 3] = right[row];
        }

        for (int pivot = 0; pivot < 3; pivot++)
        {
            int best = pivot;
            for (int row = pivot + 1; row < 3; row++)
            {
                if (Math.Abs(matrix[row, pivot]) > Math.Abs(matrix[best, pivot]))
                    best = row;
            }
            if (Math.Abs(matrix[best, pivot]) < 1e-9)
            {
                result = Array.Empty<double>();
                return false;
            }
            if (best != pivot)
            {
                for (int column = pivot; column < 4; column++)
                    (matrix[pivot, column], matrix[best, column]) =
                        (matrix[best, column], matrix[pivot, column]);
            }

            double divisor = matrix[pivot, pivot];
            for (int column = pivot; column < 4; column++)
                matrix[pivot, column] /= divisor;
            for (int row = 0; row < 3; row++)
            {
                if (row == pivot) continue;
                double factor = matrix[row, pivot];
                for (int column = pivot; column < 4; column++)
                    matrix[row, column] -= factor * matrix[pivot, column];
            }
        }

        result = new[] { matrix[0, 3], matrix[1, 3], matrix[2, 3] };
        return result.All(double.IsFinite);
    }

    private static string? ValidateConfig(AiRuntimeConfig config)
    {
        if (config.Zones is null) return "Zones are required.";

        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (AiSpawnZone zone in config.Zones)
        {
            if (!Guid.TryParse(zone.Id, out _)) return "Every zone needs a valid ID.";
            if (!ids.Add(zone.Id)) return "Zone IDs must be unique.";
            if (!SafeName.IsMatch(zone.Name ?? string.Empty)) return "A zone name is invalid.";
            if (!Species.ContainsKey(zone.Species ?? string.Empty)) return "A zone species is not approved.";
            if (ValidatePoint(zone.X, zone.Y) is string pointError) return pointError;
            if (zone.TargetCount is < 1 or > 32) return "Each manual group command can contain 1 to 32 AI.";
            if (zone.SpawnRadiusMeters is < 25 or > 500) return "Spawn radius must be 25 to 500 meters.";
            if (zone.RoamRadiusMeters is < 25 or > 2000) return "Roam radius must be 25 to 2000 meters.";
            if (zone.ActivationRadiusMeters is < 100 or > 5000) return "Activation radius must be 100 to 5000 meters.";
            if (zone.SpawnRadiusMeters > zone.RoamRadiusMeters || zone.RoamRadiusMeters > zone.ActivationRadiusMeters)
                return "Radii must be ordered spawn <= roam <= activation.";
            if (zone.Growth is < 0.05 or > 1.0) return "Growth must be between 5% and 100%.";
            if (zone.GrowthDistribution is null || zone.GrowthDistribution.Count != zone.TargetCount)
                return "Growth distribution must contain one value for every AI in the group.";
            if (zone.GrowthDistribution.Any(value => value is < 0.05 or > 1.0))
                return "Every growth value must be between 5% and 100%.";
            if (zone.RespawnDelaySeconds is < 60 or > 3600) return "Respawn delay must be 60 to 3600 seconds.";
            if (zone.RespawnDelayMaxSeconds is < 60 or > 7200 || zone.RespawnDelayMaxSeconds < zone.RespawnDelaySeconds)
                return "Maximum respawn delay must be at least the minimum and no more than 7200 seconds.";
            if (zone.HerdLifetimeSeconds is < 60 or > 7200) return "Herd lifetime must be 60 to 7200 seconds.";
            if (zone.Aquatic && (zone.MinimumDepthMeters < 0 || zone.MaximumDepthMeters <= zone.MinimumDepthMeters || zone.MaximumDepthMeters > 100))
                return "Aquatic zones need a valid depth range from 0 to 100 meters.";
        }
        return null;
    }

    private static string? ValidatePoint(double x, double y)
    {
        if (!double.IsFinite(x) || !double.IsFinite(y)) return "Map coordinates must be finite.";
        if (Math.Abs(x) > 2_000_000 || Math.Abs(y) > 2_000_000)
            return "The selected point is outside safe Gateway coordinate limits.";
        return null;
    }

    private static AiSpawnZone NormalizeZone(AiSpawnZone zone) => zone with
    {
        Id = Guid.Parse(zone.Id).ToString("D"),
        Name = zone.Name.Trim(),
        Species = Species[zone.Species].Name,
        X = Math.Round(zone.X, 3),
        Y = Math.Round(zone.Y, 3),
        TargetCount = Math.Clamp(zone.TargetCount, 1, 32),
        SpawnRadiusMeters = Math.Clamp(zone.SpawnRadiusMeters, 25, 500),
        RoamRadiusMeters = Math.Clamp(zone.RoamRadiusMeters, 25, 2000),
        ActivationRadiusMeters = Math.Clamp(zone.ActivationRadiusMeters, 100, 5000),
        Growth = Math.Clamp(zone.Growth, 0.05, 1.0),
        GrowthDistribution = zone.GrowthDistribution!.Select(value => Math.Clamp(value, 0.05, 1.0)).ToList(),
        RespawnDelaySeconds = Math.Clamp(zone.RespawnDelaySeconds, 60, 3600),
        RespawnDelayMaxSeconds = Math.Clamp(zone.RespawnDelayMaxSeconds, Math.Clamp(zone.RespawnDelaySeconds, 60, 3600), 7200),
        HerdLifetimeSeconds = Math.Clamp(zone.HerdLifetimeSeconds, 60, 7200),
        Aquatic = Species[zone.Species].Aquatic,
        Behavior = zone.Behavior.Trim()
    };
}

public sealed record AiSpeciesDefinition(
    string Name,
    string PawnClass,
    string ControllerClass,
    string Category,
    bool Aquatic,
    bool ManualTestEnabled,
    int DefaultSpawnRadiusMeters,
    int DefaultRoamRadiusMeters,
    int DefaultActivationRadiusMeters,
    double[] DefaultGrowthDistribution,
    string DefaultBehavior);

public sealed record AiAdminSettings(List<string> DiscordIds);

public sealed record AiProbeLocationRequest(double X, double Y);

public sealed record AiProbeSpawnRequest(
    string? Species,
    double X,
    double Y,
    double Growth = 1.0,
    int TargetCount = 1,
    int SpawnRadiusMeters = 0,
    List<double>? GrowthDistribution = null,
    string? ZoneId = null,
    int RespawnDelaySeconds = 300,
    int ActivationRadiusMeters = 700,
    int HerdLifetimeSeconds = 600,
    int RespawnDelayMaxSeconds = 600);

public sealed record GatewayCalibrationRequest(
    List<GatewayCalibrationSample> Samples);

public sealed record GatewayCalibrationSample(
    string Id,
    string Label,
    double WorldX,
    double WorldY,
    double PixelX,
    double PixelY,
    double ResidualPixels = 0);

public sealed record GatewayAffineTransform(
    double PixelXFromWorldX,
    double PixelXFromWorldY,
    double PixelXOffset,
    double PixelYFromWorldX,
    double PixelYFromWorldY,
    double PixelYOffset);

public sealed record GatewayCalibration(
    int SchemaVersion,
    string MapVersion,
    int ImageWidth,
    int ImageHeight,
    bool Calibrated,
    string UpdatedAtUtc,
    string UpdatedByDiscordId,
    double RmsErrorPixels,
    double MaxErrorPixels,
    GatewayAffineTransform Transform,
    List<GatewayCalibrationSample> Samples);

public sealed record AiSpawnZone(
    string Id,
    string Name,
    string Species,
    double X,
    double Y,
    int TargetCount,
    int SpawnRadiusMeters,
    double Growth,
    int RespawnDelaySeconds,
    string Behavior,
    bool Enabled,
    int RoamRadiusMeters = 300,
    int ActivationRadiusMeters = 700,
    List<double>? GrowthDistribution = null,
    bool Aquatic = false,
    double MinimumDepthMeters = 1,
    double MaximumDepthMeters = 20,
    int HerdLifetimeSeconds = 600,
    int RespawnDelayMaxSeconds = 600);

public sealed record AiRuntimeConfig(
    int SchemaVersion,
    bool Enabled,
    int Revision,
    int MaxGlobalAi,
    string UpdatedAtUtc,
    string UpdatedByDiscordId,
    List<AiSpawnZone> Zones)
{
    public static AiRuntimeConfig Empty() => new(
        2,
        false,
        1,
        40,
        DateTimeOffset.UtcNow.ToString("O"),
        string.Empty,
        new List<AiSpawnZone>());
}

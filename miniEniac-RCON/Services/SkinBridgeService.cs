using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Options;
using miniEniac_RCON.Models;

namespace miniEniac_RCON.Services;

public sealed partial class SkinBridgeService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    private readonly SkinBridgeOptions _options;
    private readonly ILogger<SkinBridgeService> _logger;
    private readonly SemaphoreSlim _appendLock = new(1, 1);

    public SkinBridgeService(
        IOptions<SkinBridgeOptions> options,
        ILogger<SkinBridgeService> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    public string CommandPath => Path.Combine(_options.ModRoot, "skin_commands.ndjson");

    public string ResultPath => Path.Combine(_options.ModRoot, "skin_results.ndjson");

    public object GetStatus()
    {
        return new
        {
            service = "miniEniac-skin-bridge",
            modRoot = _options.ModRoot,
            commandPath = CommandPath,
            resultPath = ResultPath,
            modRootExists = Directory.Exists(_options.ModRoot),
            commandFileExists = File.Exists(CommandPath),
            resultFileExists = File.Exists(ResultPath),
            timeoutSeconds = _options.TimeoutSeconds,
            pollIntervalMilliseconds = _options.PollIntervalMilliseconds
        };
    }

    public async Task<SkinBridgeResponse> InspectAsync(
        string steamId,
        CancellationToken cancellationToken = default)
    {
        ValidateSteamId(steamId);

        var commandId = Guid.NewGuid().ToString("N");
        var command = new Dictionary<string, object?>
        {
            ["id"] = commandId,
            ["verb"] = "skin.inspect",
            ["steam"] = steamId
        };

        return await SendAndWaitAsync(commandId, command, cancellationToken);
    }

    public async Task<SkinBridgeResponse> ApplyAsync(
        SkinApplyRequest request,
        CancellationToken cancellationToken = default)
    {
        ValidateSteamId(request.SteamId);
        ValidateRequestColors(request);

        if (request.PatternIndex is < 0)
        {
            throw new ArgumentException("patternIndex must be zero or greater.", nameof(request));
        }

        var commandId = Guid.NewGuid().ToString("N");
        var command = new Dictionary<string, object?>
        {
            ["id"] = commandId,
            ["verb"] = "skin",
            ["steam"] = request.SteamId,
            ["body"] = NormalizeRgb(request.Body),
            ["markings"] = NormalizeRgb(request.Markings),
            ["flank"] = NormalizeRgb(request.Flank),
            ["underbelly"] = NormalizeRgb(request.Underbelly),
            ["detail1"] = NormalizeRgb(request.Detail1),
            ["eyes"] = NormalizeRgb(request.Eyes),
            ["male_display"] = NormalizeRgb(request.MaleDisplay),
            ["teeth"] = NormalizeRgb(request.Teeth),
            ["mouth"] = NormalizeRgb(request.Mouth),
            ["claws"] = NormalizeRgb(request.Claws),
            ["pattern_index"] = request.PatternIndex,
            ["skin_variation"] = request.SkinVariation
        };

        return await SendAndWaitAsync(commandId, command, cancellationToken);
    }

    private async Task<SkinBridgeResponse> SendAndWaitAsync(
        string commandId,
        Dictionary<string, object?> command,
        CancellationToken cancellationToken)
    {
        EnsureBridgeDirectory();

        var resultStartOffset = GetCurrentFileLength(ResultPath);
        var json = JsonSerializer.Serialize(command, JsonOptions);

        await AppendCommandAsync(json, cancellationToken);

        _logger.LogInformation(
            "Queued UE4SS skin command {CommandId} with verb {Verb}",
            commandId,
            command.TryGetValue("verb", out var verb) ? verb : "unknown");

        var timeout = TimeSpan.FromSeconds(Math.Max(1, _options.TimeoutSeconds));
        var pollInterval = TimeSpan.FromMilliseconds(Math.Max(50, _options.PollIntervalMilliseconds));
        var deadline = DateTimeOffset.UtcNow + timeout;
        var nextOffset = resultStartOffset;

        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();

            var scan = await ReadNewResultsAsync(nextOffset, commandId, cancellationToken);
            nextOffset = scan.NextOffset;

            if (scan.Match is JsonElement match)
            {
                var ok = match.TryGetProperty("ok", out var okElement) && okElement.ValueKind == JsonValueKind.True;

                return new SkinBridgeResponse
                {
                    CommandId = commandId,
                    Ok = ok,
                    Result = match
                };
            }

            await Task.Delay(pollInterval, cancellationToken);
        }

        throw new SkinBridgeTimeoutException(commandId, timeout);
    }

    private async Task AppendCommandAsync(string json, CancellationToken cancellationToken)
    {
        await _appendLock.WaitAsync(cancellationToken);
        try
        {
            await using var stream = new FileStream(
                CommandPath,
                FileMode.Append,
                FileAccess.Write,
                FileShare.ReadWrite,
                bufferSize: 4096,
                useAsync: true);

            await using var writer = new StreamWriter(stream, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            await writer.WriteLineAsync(json.AsMemory(), cancellationToken);
            await writer.FlushAsync(cancellationToken);
        }
        finally
        {
            _appendLock.Release();
        }
    }

    private async Task<(long NextOffset, JsonElement? Match)> ReadNewResultsAsync(
        long startOffset,
        string commandId,
        CancellationToken cancellationToken)
    {
        if (!File.Exists(ResultPath))
        {
            return (0, null);
        }

        await using var stream = new FileStream(
            ResultPath,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete,
            bufferSize: 4096,
            useAsync: true);

        if (startOffset > stream.Length)
        {
            startOffset = 0;
        }

        stream.Seek(startOffset, SeekOrigin.Begin);

        using var reader = new StreamReader(
            stream,
            Encoding.UTF8,
            detectEncodingFromByteOrderMarks: true,
            bufferSize: 4096,
            leaveOpen: true);

        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();

            var line = await reader.ReadLineAsync(cancellationToken);
            if (line is null)
            {
                break;
            }

            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;

                if (!root.TryGetProperty("id", out var idElement) ||
                    !string.Equals(idElement.GetString(), commandId, StringComparison.Ordinal))
                {
                    continue;
                }

                return (stream.Position, root.Clone());
            }
            catch (JsonException ex)
            {
                _logger.LogWarning(ex, "Ignored malformed UE4SS skin result line");
            }
        }

        return (stream.Position, null);
    }

    private void EnsureBridgeDirectory()
    {
        if (!Directory.Exists(_options.ModRoot))
        {
            throw new DirectoryNotFoundException(
                $"miniEniac UE4SS mod directory does not exist: {_options.ModRoot}");
        }

        if (!File.Exists(CommandPath))
        {
            using var _ = File.Create(CommandPath);
        }

        if (!File.Exists(ResultPath))
        {
            using var _ = File.Create(ResultPath);
        }
    }

    private static long GetCurrentFileLength(string path)
    {
        return File.Exists(path) ? new FileInfo(path).Length : 0L;
    }

    private static void ValidateRequestColors(SkinApplyRequest request)
    {
        var colors = new Dictionary<string, string>
        {
            ["body"] = request.Body,
            ["markings"] = request.Markings,
            ["flank"] = request.Flank,
            ["underbelly"] = request.Underbelly,
            ["detail1"] = request.Detail1,
            ["eyes"] = request.Eyes,
            ["maleDisplay"] = request.MaleDisplay,
            ["teeth"] = request.Teeth,
            ["mouth"] = request.Mouth,
            ["claws"] = request.Claws
        };

        foreach (var (name, value) in colors)
        {
            if (!RgbHexRegex().IsMatch(value ?? string.Empty))
            {
                throw new ArgumentException(
                    $"{name} must be exactly six-digit RGB hex in the form #RRGGBB. Opacity is not accepted.");
            }
        }
    }

    private static string NormalizeRgb(string value)
    {
        return value.ToUpperInvariant();
    }

    private static void ValidateSteamId(string steamId)
    {
        if (!SteamIdRegex().IsMatch(steamId ?? string.Empty))
        {
            throw new ArgumentException("steamId must contain 15 to 20 digits.", nameof(steamId));
        }
    }

    [GeneratedRegex("^#[0-9A-Fa-f]{6}$", RegexOptions.CultureInvariant)]
    private static partial Regex RgbHexRegex();

    [GeneratedRegex("^[0-9]{15,20}$", RegexOptions.CultureInvariant)]
    private static partial Regex SteamIdRegex();
}

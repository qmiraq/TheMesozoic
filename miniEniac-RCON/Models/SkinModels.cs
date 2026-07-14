using System.Text.Json;
using System.Text.Json.Serialization;

namespace miniEniac_RCON.Models;

public sealed class SkinApplyRequest
{
    public required string SteamId { get; init; }

    public int? PatternIndex { get; init; }

    public int? SkinVariation { get; init; }

    public required string Body { get; init; }

    public required string Markings { get; init; }

    public required string Flank { get; init; }

    public required string Underbelly { get; init; }

    public required string Detail1 { get; init; }

    public required string Eyes { get; init; }

    public required string MaleDisplay { get; init; }

    public required string Teeth { get; init; }

    public required string Mouth { get; init; }

    public required string Claws { get; init; }
}

public sealed class SkinBridgeOptions
{
    public string ModRoot { get; set; } = @"C:\TheMesozoic\TheIsle\Binaries\Win64\Mods\miniEniac";

    public int TimeoutSeconds { get; set; } = 20;

    public int PollIntervalMilliseconds { get; set; } = 250;
}

public sealed class SkinBridgeResponse
{
    public required string CommandId { get; init; }

    public required bool Ok { get; init; }

    public required JsonElement Result { get; init; }
}

public sealed class SkinBridgeTimeoutException : TimeoutException
{
    public SkinBridgeTimeoutException(string commandId, TimeSpan timeout)
        : base($"Timed out waiting for UE4SS skin result. commandId={commandId}, timeout={timeout.TotalSeconds:0.#}s")
    {
        CommandId = commandId;
        Timeout = timeout;
    }

    public string CommandId { get; }

    public TimeSpan Timeout { get; }
}

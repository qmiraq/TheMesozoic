using System.Text.Json.Serialization;

namespace miniEniac_RCON.Models;

public sealed class DiscordOAuthOptions
{
    public string ClientId { get; set; } = "";

    public string ClientSecret { get; set; } = "";

    public string RedirectUri { get; set; } = "http://localhost:5000/Auth/discord/callback";
}

public sealed class PlayerDatabaseOptions
{
    public string DatabasePath { get; set; } = @"C:\EvrimaBot\TheMesozoic\evrimabot.db";

    public string PlayersTable { get; set; } = "players";
}

public sealed class DiscordTokenResponse
{
    [JsonPropertyName("access_token")]
    public string AccessToken { get; set; } = "";

    [JsonPropertyName("token_type")]
    public string TokenType { get; set; } = "Bearer";

    [JsonPropertyName("expires_in")]
    public int ExpiresIn { get; set; }

    [JsonPropertyName("refresh_token")]
    public string? RefreshToken { get; set; }

    [JsonPropertyName("scope")]
    public string Scope { get; set; } = "";
}

public sealed class DiscordUserResponse
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("username")]
    public string Username { get; set; } = "";

    [JsonPropertyName("global_name")]
    public string? GlobalName { get; set; }

    [JsonPropertyName("avatar")]
    public string? Avatar { get; set; }
}

using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;
using miniEniac_RCON.Models;

namespace miniEniac_RCON.Services;

public sealed class DiscordOAuthService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly DiscordOAuthOptions _options;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly ILogger<DiscordOAuthService> _logger;

    public DiscordOAuthService(
        IOptions<DiscordOAuthOptions> options,
        IHttpClientFactory httpClientFactory,
        ILogger<DiscordOAuthService> logger)
    {
        _options = options.Value;
        _httpClientFactory = httpClientFactory;
        _logger = logger;
    }

    public void ValidateConfiguration()
    {
        if (string.IsNullOrWhiteSpace(_options.ClientId))
        {
            throw new InvalidOperationException("DiscordOAuth:ClientId is not configured.");
        }

        if (string.IsNullOrWhiteSpace(_options.ClientSecret))
        {
            throw new InvalidOperationException("DiscordOAuth:ClientSecret is not configured.");
        }

        if (!Uri.TryCreate(_options.RedirectUri, UriKind.Absolute, out _))
        {
            throw new InvalidOperationException("DiscordOAuth:RedirectUri must be an absolute URL.");
        }
    }

    public string BuildAuthorizationUrl(string state)
    {
        ValidateConfiguration();

        var query = new Dictionary<string, string>
        {
            ["response_type"] = "code",
            ["client_id"] = _options.ClientId,
            ["scope"] = "identify",
            ["state"] = state,
            ["redirect_uri"] = _options.RedirectUri,
            ["prompt"] = "consent"
        };

        var encoded = string.Join("&", query.Select(pair =>
            $"{Uri.EscapeDataString(pair.Key)}={Uri.EscapeDataString(pair.Value)}"));

        return $"https://discord.com/oauth2/authorize?{encoded}";
    }

    public async Task<DiscordUserResponse> ExchangeCodeForUserAsync(
        string code,
        CancellationToken cancellationToken = default)
    {
        ValidateConfiguration();

        var client = _httpClientFactory.CreateClient(nameof(DiscordOAuthService));

        using var tokenRequest = new HttpRequestMessage(
            HttpMethod.Post,
            "https://discord.com/api/v10/oauth2/token")
        {
            Content = new FormUrlEncodedContent(new Dictionary<string, string>
            {
                ["client_id"] = _options.ClientId,
                ["client_secret"] = _options.ClientSecret,
                ["grant_type"] = "authorization_code",
                ["code"] = code,
                ["redirect_uri"] = _options.RedirectUri
            })
        };

        using var tokenResponse = await client.SendAsync(tokenRequest, cancellationToken);
        var tokenBody = await tokenResponse.Content.ReadAsStringAsync(cancellationToken);

        if (!tokenResponse.IsSuccessStatusCode)
        {
            _logger.LogWarning(
                "Discord token exchange failed with status {StatusCode}: {Body}",
                (int)tokenResponse.StatusCode,
                tokenBody);

            throw new InvalidOperationException("Discord login token exchange failed.");
        }

        var token = JsonSerializer.Deserialize<DiscordTokenResponse>(tokenBody, JsonOptions)
            ?? throw new InvalidOperationException("Discord returned an empty token response.");

        if (string.IsNullOrWhiteSpace(token.AccessToken))
        {
            throw new InvalidOperationException("Discord did not return an access token.");
        }

        using var userRequest = new HttpRequestMessage(
            HttpMethod.Get,
            "https://discord.com/api/v10/users/@me");
        userRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.AccessToken);

        using var userResponse = await client.SendAsync(userRequest, cancellationToken);
        var userBody = await userResponse.Content.ReadAsStringAsync(cancellationToken);

        if (!userResponse.IsSuccessStatusCode)
        {
            _logger.LogWarning(
                "Discord current-user request failed with status {StatusCode}: {Body}",
                (int)userResponse.StatusCode,
                userBody);

            throw new InvalidOperationException("Discord login could not read the user profile.");
        }

        var user = JsonSerializer.Deserialize<DiscordUserResponse>(userBody, JsonOptions)
            ?? throw new InvalidOperationException("Discord returned an empty user profile.");

        if (string.IsNullOrWhiteSpace(user.Id) || string.IsNullOrWhiteSpace(user.Username))
        {
            throw new InvalidOperationException("Discord returned an incomplete user profile.");
        }

        return user;
    }

    public static string? BuildAvatarUrl(DiscordUserResponse user)
    {
        if (string.IsNullOrWhiteSpace(user.Id) || string.IsNullOrWhiteSpace(user.Avatar))
        {
            return null;
        }

        var extension = user.Avatar.StartsWith("a_", StringComparison.OrdinalIgnoreCase)
            ? "gif"
            : "png";

        return $"https://cdn.discordapp.com/avatars/{Uri.EscapeDataString(user.Id)}/{Uri.EscapeDataString(user.Avatar)}.{extension}?size=128";
    }
}

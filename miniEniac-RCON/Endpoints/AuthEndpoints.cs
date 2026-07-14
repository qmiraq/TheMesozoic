using System.Security.Claims;
using System.Security.Cryptography;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.Data.Sqlite;
using miniEniac_RCON.Services;

namespace miniEniac_RCON.Endpoints;

public static class AuthEndpoints
{
    private const string OAuthStateCookie = ".Mesozoic.DiscordOAuth.State";
    private const string DiscordUsernameClaim = "discord_username";
    private const string DiscordAvatarClaim = "discord_avatar";

    public static IEndpointRouteBuilder MapDiscordAuthEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/Auth")
            .WithTags("Authentication");

        group.MapGet("/discord/login", (
            HttpContext context,
            DiscordOAuthService discordOAuth) =>
        {
            try
            {
                var state = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();

                context.Response.Cookies.Append(
                    OAuthStateCookie,
                    state,
                    new CookieOptions
                    {
                        HttpOnly = true,
                        Secure = context.Request.IsHttps,
                        SameSite = SameSiteMode.Lax,
                        MaxAge = TimeSpan.FromMinutes(10),
                        Path = "/Auth/discord"
                    });

                return Results.Redirect(discordOAuth.BuildAuthorizationUrl(state));
            }
            catch (InvalidOperationException ex)
            {
                return Results.Json(
                    new { error = ex.Message },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
            }
        });

        group.MapGet("/discord/callback", async (
            HttpContext context,
            DiscordOAuthService discordOAuth,
            string? code,
            string? state,
            string? error,
            CancellationToken cancellationToken) =>
        {
            if (!string.IsNullOrWhiteSpace(error))
            {
                return Results.Redirect("/?auth=cancelled");
            }

            if (string.IsNullOrWhiteSpace(code) || string.IsNullOrWhiteSpace(state))
            {
                return Results.Redirect("/?auth=invalid-callback");
            }

            context.Request.Cookies.TryGetValue(OAuthStateCookie, out var expectedState);
            var expectedStateBytes = string.IsNullOrWhiteSpace(expectedState)
                ? Array.Empty<byte>()
                : System.Text.Encoding.UTF8.GetBytes(expectedState);
            var returnedStateBytes = System.Text.Encoding.UTF8.GetBytes(state);

            if (!context.Request.Cookies.ContainsKey(OAuthStateCookie)
                || expectedStateBytes.Length != returnedStateBytes.Length
                || !CryptographicOperations.FixedTimeEquals(expectedStateBytes, returnedStateBytes))
            {
                return Results.Redirect("/?auth=invalid-state");
            }

            context.Response.Cookies.Delete(
                OAuthStateCookie,
                new CookieOptions { Path = "/Auth/discord" });

            try
            {
                var discordUser = await discordOAuth.ExchangeCodeForUserAsync(code, cancellationToken);
                var displayName = string.IsNullOrWhiteSpace(discordUser.GlobalName)
                    ? discordUser.Username
                    : discordUser.GlobalName;
                var avatarUrl = DiscordOAuthService.BuildAvatarUrl(discordUser);

                var claims = new List<Claim>
                {
                    new(ClaimTypes.NameIdentifier, discordUser.Id),
                    new(ClaimTypes.Name, displayName),
                    new(DiscordUsernameClaim, discordUser.Username)
                };

                if (!string.IsNullOrWhiteSpace(avatarUrl))
                {
                    claims.Add(new Claim(DiscordAvatarClaim, avatarUrl));
                }

                var identity = new ClaimsIdentity(
                    claims,
                    CookieAuthenticationDefaults.AuthenticationScheme);

                await context.SignInAsync(
                    CookieAuthenticationDefaults.AuthenticationScheme,
                    new ClaimsPrincipal(identity),
                    new AuthenticationProperties
                    {
                        IsPersistent = true,
                        AllowRefresh = true,
                        ExpiresUtc = DateTimeOffset.UtcNow.AddDays(7)
                    });

                return Results.Redirect("/");
            }
            catch (Exception ex) when (ex is InvalidOperationException or HttpRequestException)
            {
                return Results.Redirect("/?auth=discord-error");
            }
        });

        group.MapGet("/me", async (
            ClaimsPrincipal user,
            PlayerLinkService playerLinks,
            CancellationToken cancellationToken) =>
        {
            if (user.Identity?.IsAuthenticated != true)
            {
                return Results.Unauthorized();
            }

            var discordId = user.FindFirstValue(ClaimTypes.NameIdentifier);
            if (string.IsNullOrWhiteSpace(discordId))
            {
                return Results.Unauthorized();
            }

            bool hasSteamLink;
            try
            {
                hasSteamLink = await playerLinks.FindSteamIdByDiscordIdAsync(discordId, cancellationToken) is not null;
            }
            catch (Exception ex) when (ex is IOException or InvalidOperationException or SqliteException)
            {
                return Results.Json(
                    new { error = "Player database is unavailable.", detail = ex.Message },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
            }

            return Results.Ok(new
            {
                discordId,
                displayName = user.FindFirstValue(ClaimTypes.Name) ?? "Discord user",
                username = user.FindFirstValue(DiscordUsernameClaim),
                avatarUrl = user.FindFirstValue(DiscordAvatarClaim),
                hasSteamLink
            });
        });

        group.MapPost("/logout", async (HttpContext context) =>
        {
            await context.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
            return Results.Ok(new { ok = true });
        }).RequireAuthorization();

        return app;
    }
}

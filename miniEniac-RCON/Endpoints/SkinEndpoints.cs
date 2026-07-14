using System.Security.Claims;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;
using miniEniac_RCON.Models;
using miniEniac_RCON.Services;

namespace miniEniac_RCON.Endpoints;

public static class SkinEndpoints
{
    public static IEndpointRouteBuilder MapSkinEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/Skin")
            .WithTags("Skin");

        // Public website routes: the authenticated Discord user is resolved to a
        // linked SteamID server-side. The browser never gets to choose a SteamID.
        group.MapGet("/me", InspectOwnSkinAsync)
            .RequireAuthorization();

        group.MapPost("/me/apply", ApplyOwnSkinAsync)
            .RequireAuthorization();

        // Legacy/manual endpoints are useful while developing, but they must remain
        // disabled on a public deployment because they accept arbitrary SteamIDs.
        var webOptions = app.ServiceProvider
            .GetRequiredService<IOptions<SkinWebOptions>>()
            .Value;

        if (webOptions.EnableDevelopmentEndpoints)
        {
            group.MapGet("/status", (SkinBridgeService bridge) =>
                Results.Ok(bridge.GetStatus()));

            group.MapGet("/inspect/{steamId}", async (
                string steamId,
                SkinBridgeService bridge,
                CancellationToken cancellationToken) =>
            {
                try
                {
                    var response = await bridge.InspectAsync(steamId, cancellationToken);
                    return response.Ok
                        ? Results.Ok(response.Result)
                        : Results.BadRequest(response.Result);
                }
                catch (Exception ex)
                {
                    return MapSkinException(ex);
                }
            });

            group.MapPost("/apply", async (
                SkinApplyRequest request,
                SkinBridgeService bridge,
                CancellationToken cancellationToken) =>
            {
                try
                {
                    var response = await bridge.ApplyAsync(request, cancellationToken);
                    return response.Ok
                        ? Results.Ok(response.Result)
                        : Results.BadRequest(response.Result);
                }
                catch (Exception ex)
                {
                    return MapSkinException(ex);
                }
            });
        }

        return app;
    }

    private static async Task<IResult> InspectOwnSkinAsync(
        ClaimsPrincipal user,
        PlayerLinkService playerLinks,
        SkinBridgeService bridge,
        CancellationToken cancellationToken)
    {
        try
        {
            var steamId = await ResolveLinkedSteamIdAsync(user, playerLinks, cancellationToken);
            if (steamId is null)
            {
                return Results.NotFound(new
                {
                    error = "No SteamID is linked to your Discord account.",
                    code = "steam-link-missing"
                });
            }

            var response = await bridge.InspectAsync(steamId, cancellationToken);
            return response.Ok
                ? Results.Ok(response.Result)
                : Results.BadRequest(response.Result);
        }
        catch (Exception ex)
        {
            return MapSkinException(ex);
        }
    }

    private static async Task<IResult> ApplyOwnSkinAsync(
        ClaimsPrincipal user,
        SkinSelfApplyRequest request,
        PlayerLinkService playerLinks,
        SkinBridgeService bridge,
        CancellationToken cancellationToken)
    {
        try
        {
            var steamId = await ResolveLinkedSteamIdAsync(user, playerLinks, cancellationToken);
            if (steamId is null)
            {
                return Results.NotFound(new
                {
                    error = "No SteamID is linked to your Discord account.",
                    code = "steam-link-missing"
                });
            }

            var response = await bridge.ApplyAsync(request.ForSteamId(steamId), cancellationToken);
            return response.Ok
                ? Results.Ok(response.Result)
                : Results.BadRequest(response.Result);
        }
        catch (Exception ex)
        {
            return MapSkinException(ex);
        }
    }

    private static async Task<string?> ResolveLinkedSteamIdAsync(
        ClaimsPrincipal user,
        PlayerLinkService playerLinks,
        CancellationToken cancellationToken)
    {
        var discordId = user.FindFirstValue(ClaimTypes.NameIdentifier);
        if (string.IsNullOrWhiteSpace(discordId))
        {
            throw new UnauthorizedAccessException("Discord identity is missing from the authenticated session.");
        }

        return await playerLinks.FindSteamIdByDiscordIdAsync(discordId, cancellationToken);
    }

    private static IResult MapSkinException(Exception ex) => ex switch
    {
        UnauthorizedAccessException => Results.Unauthorized(),
        ArgumentException argument => Results.BadRequest(new { error = argument.Message }),
        SkinBridgeTimeoutException timeout => Results.Json(
            new { error = timeout.Message, commandId = timeout.CommandId },
            statusCode: StatusCodes.Status504GatewayTimeout),
        DirectoryNotFoundException directory => Results.Json(
            new { error = directory.Message },
            statusCode: StatusCodes.Status503ServiceUnavailable),
        FileNotFoundException file => Results.Json(
            new { error = file.Message },
            statusCode: StatusCodes.Status503ServiceUnavailable),
        SqliteException sqlite => Results.Json(
            new { error = "Player database query failed.", detail = sqlite.Message },
            statusCode: StatusCodes.Status503ServiceUnavailable),
        IOException io => Results.Json(
            new { error = "Skin bridge or player database file I/O failed.", detail = io.Message },
            statusCode: StatusCodes.Status503ServiceUnavailable),
        InvalidOperationException invalid => Results.Json(
            new { error = invalid.Message },
            statusCode: StatusCodes.Status503ServiceUnavailable),
        _ => Results.Json(
            new { error = "Unexpected skin service error." },
            statusCode: StatusCodes.Status500InternalServerError)
    };
}

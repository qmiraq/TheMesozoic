using miniEniac_RCON.Models;
using miniEniac_RCON.Services;

namespace miniEniac_RCON.Endpoints;

public static class SkinEndpoints
{
    public static IEndpointRouteBuilder MapSkinEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/Skin")
            .WithTags("Skin");

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
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
            catch (SkinBridgeTimeoutException ex)
            {
                return Results.Json(
                    new { error = ex.Message, commandId = ex.CommandId },
                    statusCode: StatusCodes.Status504GatewayTimeout);
            }
            catch (DirectoryNotFoundException ex)
            {
                return Results.Json(
                    new { error = ex.Message },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
            }
            catch (IOException ex)
            {
                return Results.Json(
                    new { error = "Skin bridge file I/O failed.", detail = ex.Message },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
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
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
            catch (SkinBridgeTimeoutException ex)
            {
                return Results.Json(
                    new { error = ex.Message, commandId = ex.CommandId },
                    statusCode: StatusCodes.Status504GatewayTimeout);
            }
            catch (DirectoryNotFoundException ex)
            {
                return Results.Json(
                    new { error = ex.Message },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
            }
            catch (IOException ex)
            {
                return Results.Json(
                    new { error = "Skin bridge file I/O failed.", detail = ex.Message },
                    statusCode: StatusCodes.Status503ServiceUnavailable);
            }
        });

        return app;
    }
}

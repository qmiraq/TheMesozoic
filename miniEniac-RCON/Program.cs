using Microsoft.AspNetCore.HttpOverrides;
using System.Net;
using Microsoft.AspNetCore.Authentication.Cookies;
using miniEniac_RCON.Endpoints;
using miniEniac_RCON.Services;
using miniEniac_RCON.Models;
var builder = WebApplication.CreateBuilder(args);
builder.Services.Configure<ForwardedHeadersOptions>(options =>
{
    options.ForwardedHeaders =
        ForwardedHeaders.XForwardedFor |
        ForwardedHeaders.XForwardedProto;

    // Only the local Caddy reverse proxy is trusted.
    options.KnownProxies.Add(IPAddress.Loopback);
    options.KnownProxies.Add(IPAddress.IPv6Loopback);
});

// Add services to the container.

builder.Services.AddControllers();
builder.Services.AddSingleton<EvrimaRconService>();

builder.Services.AddSingleton<SkinBridgeService>();

builder.Services.Configure<SkinBridgeOptions>(
    builder.Configuration.GetSection("SkinBridge"));

builder.Services.Configure<SkinWebOptions>(
    builder.Configuration.GetSection("SkinWeb"));

builder.Services.Configure<DiscordOAuthOptions>(
    builder.Configuration.GetSection("DiscordOAuth"));

builder.Services.Configure<PlayerDatabaseOptions>(
    builder.Configuration.GetSection("PlayerDatabase"));

builder.Services.AddSingleton<PlayerLinkService>();
builder.Services.AddSingleton<DiscordOAuthService>();
builder.Services.AddHttpClient();

builder.Services
    .AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.Cookie.Name = ".TheMesozoic.Auth";
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
        options.Cookie.SecurePolicy = CookieSecurePolicy.Always;
        options.ExpireTimeSpan = TimeSpan.FromDays(7);
        options.SlidingExpiration = true;

        // API routes should return HTTP 401/403 instead of redirecting fetch()
        // calls to an HTML login page.
        options.Events.OnRedirectToLogin = context =>
        {
            if (context.Request.Path.StartsWithSegments("/Auth")
                || context.Request.Path.StartsWithSegments("/Skin"))
            {
                context.Response.StatusCode = StatusCodes.Status401Unauthorized;
                return Task.CompletedTask;
            }

            context.Response.Redirect(context.RedirectUri);
            return Task.CompletedTask;
        };

        options.Events.OnRedirectToAccessDenied = context =>
        {
            context.Response.StatusCode = StatusCodes.Status403Forbidden;
            return Task.CompletedTask;
        };
    });

builder.Services.AddAuthorization();

var app = builder.Build();
// Configure the HTTP request pipeline.
app.UseForwardedHeaders();

app.UseDefaultFiles();

app.UseStaticFiles();

app.UseHttpsRedirection();

app.UseAuthentication();

app.UseAuthorization();

app.MapControllers();

app.MapDiscordAuthEndpoints();

app.MapSkinEndpoints();

app.MapSkinPresetEndpoints();

app.Run();

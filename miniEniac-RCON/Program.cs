using miniEniac_RCON.Services;
using miniEniac_RCON.Endpoints;
using miniEniac_RCON.Models;
var builder = WebApplication.CreateBuilder(args);

// Add services to the container.

builder.Services.AddControllers();
builder.Services.AddSingleton<EvrimaRconService>();
builder.Services.Configure<SkinBridgeOptions>(
    builder.Configuration.GetSection("SkinBridge"));

builder.Services.AddSingleton<SkinBridgeService>();

var app = builder.Build();

// Configure the HTTP request pipeline.

app.UseHttpsRedirection();

app.UseAuthorization();

app.MapControllers();

app.MapSkinEndpoints();

app.Run();

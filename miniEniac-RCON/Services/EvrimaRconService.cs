using System.Net;
using miniEniac_RCON.Models;
using TheIsleEvrimaRconClient;
using System.Globalization;

namespace miniEniac_RCON.Services;

public class EvrimaRconService
{
    private readonly IConfiguration _configuration;

    public EvrimaRconService(IConfiguration configuration)
    {
        _configuration = configuration;
    }

    private EvrimaRconClient CreateClient()
    {
        var config = new EvrimaRconClientConfiguration
        {
            Host = IPAddress.Parse(_configuration["EvrimaRcon:Host"]!),
            Port = int.Parse(_configuration["EvrimaRcon:Port"]!),
            Password = _configuration["EvrimaRcon:Password"]!
        };

        return new EvrimaRconClient(config);
    }

    private string GetValue(string input, string key)
    {
        var start = input.IndexOf(key + ":");

        if (start == -1)
            return "";

        start += key.Length + 1;

        var end = input.IndexOf(", ", start);

        if (end == -1)
            end = input.Length;

        return input[start..end].Trim();
    }

    public async Task<List<PlayerResponse>> GetPlayers()
    {
        using var client = CreateClient();

        bool connected = await client.ConnectAsync();

        if (!connected)
        {
            throw new Exception("Could not connect to RCON");
        }

        var response = await client.SendCommandAsync(
            EvrimaRconCommand.PlayerList
        );

        var players = new List<PlayerResponse>();

        var lines = response.Split(
            '\n',
            StringSplitOptions.RemoveEmptyEntries
        );

        for (int i = 1; i < lines.Length; i += 2)
        {
            if (i + 1 >= lines.Length)
                break;

            players.Add(new PlayerResponse
            {
                SteamId = lines[i].Trim().Trim(','),
                Name = lines[i + 1].Trim().Trim(',')
            });
        }

        return players;
    }

    public async Task<ServerDetailsResponse> GetServerDetails()
    {
        using var client = CreateClient();

        bool connected = await client.ConnectAsync();

        if (!connected)
        {
            throw new Exception("Could not connect to RCON");
        }

        var response = await client.SendCommandAsync(
            EvrimaRconCommand.ServerDetails
        );

        var server = new ServerDetailsResponse();

        server.Name = GetValue(response, "ServerName");
        server.Map = GetValue(response, "ServerMap");

        server.MaxPlayers = int.Parse(
            GetValue(response, "ServerMaxPlayers")
        );

        server.CurrentPlayers = int.Parse(
            GetValue(response, "ServerCurrentPlayers")
        );

        server.MutationsEnabled = bool.Parse(
            GetValue(response, "bEnableMutations")
        );

        server.HumansEnabled = bool.Parse(
            GetValue(response, "bEnableHumans")
        );

        server.PasswordProtected = bool.Parse(
            GetValue(response, "bServerPassword")
        );

        server.QueueEnabled = bool.Parse(
            GetValue(response, "bQueueEnabled")
        );

        server.WhitelistEnabled = bool.Parse(
            GetValue(response, "bServerWhitelist")
        );

        server.SpawnAIEnabled = bool.Parse(
            GetValue(response, "bSpawnAI")
        );

        server.DayLengthMinutes = int.Parse(
            GetValue(response, "ServerDayLengthMinutes")
        );

        server.NightLengthMinutes = int.Parse(
            GetValue(response, "ServerNightLengthMinutes")
        );

        server.GlobalChatEnabled = bool.Parse(
            GetValue(response, "bEnableGlobalChat")
        );

        return server;
    }

    public async Task<List<PlayerDataResponse>> GetPlayerData()
    {
        using var client = CreateClient();

        bool connected = await client.ConnectAsync();

        if (!connected)
        {
            throw new Exception("Could not connect to RCON");
        }

        var response = await client.SendCommandAsync(
            EvrimaRconCommand.GetPlayerData
        );


        var players = new List<PlayerDataResponse>();


        var lines = response.Split(
            '\n',
            StringSplitOptions.RemoveEmptyEntries
        );


        foreach (var line in lines)
        {
            if (!line.StartsWith("Name:"))
                continue;


            var player = new PlayerDataResponse();


            player.Name = GetValue(line, "Name");
            player.SteamId = GetValue(line, "PlayerID");
            player.Gender = GetValue(line, "Gender");
            player.Class = GetValue(line, "Class");


            double.TryParse(
                GetValue(line, "Growth"),
                NumberStyles.Any,
                CultureInfo.InvariantCulture,
                out double growth
            );

            player.Growth = growth;


            double.TryParse(
                GetValue(line, "Health"),
                NumberStyles.Any,
                CultureInfo.InvariantCulture,
                out double health
            );

            player.Health = health;


            double.TryParse(
                GetValue(line, "Stamina"),
                NumberStyles.Any,
                CultureInfo.InvariantCulture,
                out double stamina
            );

            player.Stamina = stamina;


            double.TryParse(
                GetValue(line, "Hunger"),
                NumberStyles.Any,
                CultureInfo.InvariantCulture,
                out double hunger
            );

            player.Hunger = hunger;


            double.TryParse(
                GetValue(line, "Thirst"),
                NumberStyles.Any,
                CultureInfo.InvariantCulture,
                out double thirst
            );

            player.Thirst = thirst;


            player.PrimeElder =
                bool.TryParse(
                    GetValue(line, "PrimeElder"),
                    out bool prime
                ) && prime;


            var locationText = GetValue(line, "Location");

            if (!string.IsNullOrEmpty(locationText))
            {
                locationText = locationText
                    .Replace("X=", "")
                    .Replace("Y=", "")
                    .Replace("Z=", "");

                var coords = locationText.Split(
                    ' ',
                    StringSplitOptions.RemoveEmptyEntries
                );


                if (coords.Length == 3)
                {
                    double.TryParse(
                        coords[0],
                        NumberStyles.Any,
                        CultureInfo.InvariantCulture,
                        out double x
                    );

                    double.TryParse(
                        coords[1],
                        NumberStyles.Any,
                        CultureInfo.InvariantCulture,
                        out double y
                    );

                    double.TryParse(
                        coords[2],
                        NumberStyles.Any,
                        CultureInfo.InvariantCulture,
                        out double z
                    );


                    player.Location = new PlayerLocation
                    {
                        X = x,
                        Y = y,
                        Z = z
                    };
                }
            }


            players.Add(player);
        }


        return players;
    }
}
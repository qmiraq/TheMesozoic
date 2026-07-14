using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;
using miniEniac_RCON.Models;

namespace miniEniac_RCON.Services;

public sealed partial class PlayerLinkService
{
    private readonly PlayerDatabaseOptions _options;
    private readonly ILogger<PlayerLinkService> _logger;

    public PlayerLinkService(
        IOptions<PlayerDatabaseOptions> options,
        ILogger<PlayerLinkService> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    public async Task<string?> FindSteamIdByDiscordIdAsync(
        string discordId,
        CancellationToken cancellationToken = default)
    {
        if (!DiscordIdRegex().IsMatch(discordId))
        {
            throw new ArgumentException("Authenticated Discord ID is invalid.", nameof(discordId));
        }

        if (!File.Exists(_options.DatabasePath))
        {
            throw new FileNotFoundException(
                $"Player database was not found at '{_options.DatabasePath}'.",
                _options.DatabasePath);
        }

        var table = ValidateIdentifier(_options.PlayersTable, nameof(_options.PlayersTable));
        var connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = _options.DatabasePath,
            Mode = SqliteOpenMode.ReadOnly,
            Cache = SqliteCacheMode.Shared
        }.ToString();

        await using var connection = new SqliteConnection(connectionString);
        await connection.OpenAsync(cancellationToken);

        await using var command = connection.CreateCommand();
        command.CommandText = $"SELECT steam_id FROM \"{table}\" WHERE discord_id = $discordId LIMIT 1;";
        command.Parameters.AddWithValue("$discordId", discordId);

        var result = await command.ExecuteScalarAsync(cancellationToken);
        var steamId = result?.ToString()?.Trim();

        if (string.IsNullOrWhiteSpace(steamId))
        {
            return null;
        }

        if (!SteamIdRegex().IsMatch(steamId))
        {
            _logger.LogWarning(
                "Player link for Discord user {DiscordId} returned invalid SteamID {SteamId}",
                discordId,
                steamId);
            return null;
        }

        return steamId;
    }

    private static string ValidateIdentifier(string value, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(value) || !SqlIdentifierRegex().IsMatch(value))
        {
            throw new InvalidOperationException(
                $"{parameterName} must contain only letters, digits, and underscores and cannot begin with a digit.");
        }

        return value;
    }

    [GeneratedRegex("^[0-9]{15,22}$")]
    private static partial Regex DiscordIdRegex();

    [GeneratedRegex("^[0-9]{15,20}$")]
    private static partial Regex SteamIdRegex();

    [GeneratedRegex("^[A-Za-z_][A-Za-z0-9_]*$")]
    private static partial Regex SqlIdentifierRegex();
}

using System.Security.Claims;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;

namespace miniEniac_RCON.Endpoints;

public sealed record SkinPresetSkinData(
    int PatternIndex,
    int SkinVariation,
    bool IsFemale,
    string Body,
    string Markings,
    string Flank,
    string Underbelly,
    string Detail1,
    string Eyes,
    string MaleDisplay,
    string Teeth,
    string Mouth,
    string Claws);

public sealed record SaveSkinPresetRequest(
    string Name,
    string Species,
    SkinPresetSkinData Skin);

public sealed record RenameSkinPresetRequest(string Name);

public sealed record UpdateSkinPresetRequest(SkinPresetSkinData Skin);

public sealed record CopySkinPresetRequest(string TargetSpecies);

public sealed record SkinPresetResponse(
    long Id,
    string Name,
    string Species,
    SkinPresetSkinData Skin,
    string CreatedAt,
    string UpdatedAt);

public sealed class SkinPresetLimitException : Exception
{
    public SkinPresetLimitException(string species, int limit)
        : base($"You already have {limit} presets saved for {species}.")
    {
        Species = species;
        Limit = limit;
    }

    public string Species { get; }

    public int Limit { get; }
}

public static class SkinPresetEndpoints
{
    public const int MaximumPresetsPerSpecies = 40;

    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web);

    private static readonly Regex ColorPattern =
        new(
            "^#[0-9A-Fa-f]{6}$",
            RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly string[] CopySpecies =
    new[]
    {
        "Allosaurus",
        "Austroraptor",
        "Beipiaosaurus",
        "Carnotaurus",
        "Ceratosaurus",
        "Deinosuchus",
        "Diabloceratops",
        "Dilophosaurus",
        "Dryosaurus",
        "Gallimimus",
        "Herrerasaurus",
        "Hypsilophodon",
        "Kentrosaurus",
        "Maiasaura",
        "Omniraptor",
        "Pachycephalosaurus",
        "Pteranodon",
        "Stegosaurus",
        "Tenontosaurus",
        "Triceratops",
        "Troodon",
        "Tyrannosaurus"
    };

    private static readonly IReadOnlyDictionary<string, int> PatternCounts =
        new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
        {
            ["Allosaurus"] = 4,
            ["Omniraptor"] = 6,
            ["Austroraptor"] = 4,
            ["Carnotaurus"] = 4,
            ["Herrerasaurus"] = 5,
            ["Pachycephalosaurus"] = 5,
            ["Stegosaurus"] = 4,
            ["Triceratops"] = 6,
            ["Tyrannosaurus"] = 5
        };

    public static IEndpointRouteBuilder MapSkinPresetEndpoints(
        this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/Skin/presets")
            .WithTags("Skin Presets")
            .RequireAuthorization();

        group.MapGet("/pattern-counts", () =>
            Results.Ok(CopySpecies.ToDictionary(
                species => species,
                species => PatternCounts.TryGetValue(species, out var count)
                    ? count
                    : 3,
                StringComparer.OrdinalIgnoreCase)));

        group.MapGet("", async (
            ClaimsPrincipal user,
            IConfiguration configuration,
            string? species,
            CancellationToken cancellationToken) =>
        {
            try
            {
                var discordId = GetDiscordId(user);
                var normalizedSpecies = NormalizeSpecies(species);

                await using var connection =
                    await OpenDatabaseAsync(configuration, cancellationToken);

                var presets = await ListPresetsAsync(
                    connection,
                    discordId,
                    normalizedSpecies,
                    cancellationToken);

                return Results.Ok(presets);
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }
        });

        group.MapPost("", async (
            ClaimsPrincipal user,
            SaveSkinPresetRequest request,
            IConfiguration configuration,
            CancellationToken cancellationToken) =>
        {
            try
            {
                var discordId = GetDiscordId(user);
                var name = NormalizeName(request.Name);
                var species = NormalizeSpecies(request.Species);
                var skin = NormalizeSkin(request.Skin);

                await using var connection =
                    await OpenDatabaseAsync(configuration, cancellationToken);

                var preset = await SavePresetAsync(
                    connection,
                    discordId,
                    name,
                    species,
                    skin,
                    cancellationToken);

                return Results.Ok(preset);
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }
        });

        group.MapPatch("/{id:long}/name", async (
            long id,
            ClaimsPrincipal user,
            RenameSkinPresetRequest request,
            IConfiguration configuration,
            CancellationToken cancellationToken) =>
        {
            try
            {
                var discordId = GetDiscordId(user);
                var name = NormalizeName(request.Name);

                await using var connection =
                    await OpenDatabaseAsync(configuration, cancellationToken);

                var preset = await RenamePresetAsync(
                    connection,
                    discordId,
                    id,
                    name,
                    cancellationToken);

                return preset is null
                    ? Results.NotFound(new { error = "Preset not found." })
                    : Results.Ok(preset);
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }
        });

        group.MapPatch("/{id:long}/skin", async (
            long id,
            ClaimsPrincipal user,
            UpdateSkinPresetRequest request,
            IConfiguration configuration,
            CancellationToken cancellationToken) =>
        {
            try
            {
                var discordId = GetDiscordId(user);
                var skin = NormalizeSkin(request.Skin);

                await using var connection =
                    await OpenDatabaseAsync(configuration, cancellationToken);

                var preset = await UpdatePresetSkinAsync(
                    connection,
                    discordId,
                    id,
                    skin,
                    cancellationToken);

                return preset is null
                    ? Results.NotFound(new { error = "Preset not found." })
                    : Results.Ok(preset);
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }
        });

        group.MapPost("/{id:long}/copy", async (
            long id,
            ClaimsPrincipal user,
            CopySkinPresetRequest request,
            IConfiguration configuration,
            CancellationToken cancellationToken) =>
        {
            try
            {
                var discordId = GetDiscordId(user);

                await using var connection =
                    await OpenDatabaseAsync(configuration, cancellationToken);

                var sourcePreset = await GetOwnedPresetAsync(
                    connection,
                    discordId,
                    id,
                    cancellationToken);

                if (sourcePreset is null)
                {
                    return Results.NotFound(new { error = "Preset not found." });
                }

                var requestedTarget = request.TargetSpecies?.Trim() ?? "";
                var copyAll = requestedTarget == "*";

                var targets = copyAll
                    ? CopySpecies
                        .Where(species => !string.Equals(
                            species,
                            sourcePreset.Species,
                            StringComparison.OrdinalIgnoreCase))
                        .ToArray()
                    : new[] { NormalizeCopySpecies(requestedTarget) };

                if (!copyAll && string.Equals(
                    targets[0],
                    sourcePreset.Species,
                    StringComparison.OrdinalIgnoreCase))
                {
                    throw new ArgumentException(
                        "Choose a different dinosaur species.");
                }

                var copiedSpecies = new List<string>();
                var skippedSpecies = new List<string>();

                foreach (var targetSpecies in targets)
                {
                    try
                    {
                        var targetSkin = AdaptSkinForSpecies(
                            sourcePreset.Skin,
                            targetSpecies);

                        await SavePresetAsync(
                            connection,
                            discordId,
                            sourcePreset.Name,
                            targetSpecies,
                            targetSkin,
                            cancellationToken);

                        copiedSpecies.Add(targetSpecies);
                    }
                    catch (SkinPresetLimitException) when (copyAll)
                    {
                        skippedSpecies.Add(targetSpecies);
                    }
                }

                return Results.Ok(new
                {
                    sourcePresetId = sourcePreset.Id,
                    sourceSpecies = sourcePreset.Species,
                    name = sourcePreset.Name,
                    copiedCount = copiedSpecies.Count,
                    copiedSpecies,
                    skippedCount = skippedSpecies.Count,
                    skippedSpecies
                });
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }
        });

        group.MapDelete("/{id:long}", async (
            long id,
            ClaimsPrincipal user,
            IConfiguration configuration,
            CancellationToken cancellationToken) =>
        {
            try
            {
                var discordId = GetDiscordId(user);

                await using var connection =
                    await OpenDatabaseAsync(configuration, cancellationToken);

                var deleted = await DeletePresetAsync(
                    connection,
                    discordId,
                    id,
                    cancellationToken);

                return deleted
                    ? Results.NoContent()
                    : Results.NotFound(new { error = "Preset not found." });
            }
            catch (Exception ex)
            {
                return MapException(ex);
            }
        });

        return app;
    }

    private static string GetDiscordId(ClaimsPrincipal user)
    {
        var discordId = user.FindFirstValue(ClaimTypes.NameIdentifier);

        if (string.IsNullOrWhiteSpace(discordId))
        {
            throw new UnauthorizedAccessException(
                "Discord identity is missing from the authenticated session.");
        }

        return discordId;
    }

    private static async Task<SqliteConnection> OpenDatabaseAsync(
        IConfiguration configuration,
        CancellationToken cancellationToken)
    {
        var configuredPresetPath =
            configuration["SkinPresets:DatabasePath"];

        string databasePath;

        if (!string.IsNullOrWhiteSpace(configuredPresetPath))
        {
            databasePath = Path.IsPathRooted(configuredPresetPath)
                ? configuredPresetPath
                : Path.GetFullPath(
                    Path.Combine(
                        AppContext.BaseDirectory,
                        configuredPresetPath));
        }
        else
        {
            var configuredPlayerDatabase =
                configuration["PlayerDatabase:DatabasePath"];

            var playerDatabasePath =
                !string.IsNullOrWhiteSpace(configuredPlayerDatabase)
                    ? configuredPlayerDatabase
                    : Path.Combine(
                        AppContext.BaseDirectory,
                        "evrimabot.db");

            if (!Path.IsPathRooted(playerDatabasePath))
            {
                playerDatabasePath = Path.GetFullPath(
                    Path.Combine(
                        AppContext.BaseDirectory,
                        playerDatabasePath));
            }

            var databaseDirectory =
                Path.GetDirectoryName(playerDatabasePath)
                ?? AppContext.BaseDirectory;

            databasePath = Path.Combine(
                databaseDirectory,
                "skin_presets.db");
        }

        var directory = Path.GetDirectoryName(databasePath);

        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = databasePath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
            Pooling = true
        }.ToString();

        var connection = new SqliteConnection(connectionString);

        try
        {
            await connection.OpenAsync(cancellationToken);

            await using (var busyCommand = connection.CreateCommand())
            {
                busyCommand.CommandText =
                    "PRAGMA busy_timeout = 5000;";

                await busyCommand.ExecuteNonQueryAsync(
                    cancellationToken);
            }

            await using (var journalCommand = connection.CreateCommand())
            {
                journalCommand.CommandText =
                    "PRAGMA journal_mode = WAL;";

                await journalCommand.ExecuteScalarAsync(
                    cancellationToken);
            }

            await using (var syncCommand = connection.CreateCommand())
            {
                syncCommand.CommandText =
                    "PRAGMA synchronous = NORMAL;";

                await syncCommand.ExecuteNonQueryAsync(
                    cancellationToken);
            }

            await EnsureSchemaAsync(
                connection,
                cancellationToken);

            return connection;
        }
        catch
        {
            await connection.DisposeAsync();
            throw;
        }
    }

    private static async Task EnsureSchemaAsync(
        SqliteConnection connection,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();

        command.CommandText =
            """
            CREATE TABLE IF NOT EXISTS skin_presets
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                species TEXT COLLATE NOCASE NOT NULL,
                name TEXT COLLATE NOCASE NOT NULL,
                skin_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(discord_id, species, name)
            );

            CREATE INDEX IF NOT EXISTS ix_skin_presets_owner_species
            ON skin_presets(discord_id, species);
            """;

        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task<List<SkinPresetResponse>> ListPresetsAsync(
        SqliteConnection connection,
        string discordId,
        string species,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();

        command.CommandText =
            """
            SELECT
                id,
                name,
                species,
                skin_json,
                created_at,
                updated_at
            FROM skin_presets
            WHERE discord_id = $discordId
              AND species = $species COLLATE NOCASE
            ORDER BY updated_at DESC, id DESC;
            """;

        command.Parameters.AddWithValue("$discordId", discordId);
        command.Parameters.AddWithValue("$species", species);

        var results = new List<SkinPresetResponse>();

        await using var reader =
            await command.ExecuteReaderAsync(cancellationToken);

        while (await reader.ReadAsync(cancellationToken))
        {
            results.Add(ReadPreset(reader));
        }

        return results;
    }

    private static async Task<SkinPresetResponse> SavePresetAsync(
        SqliteConnection connection,
        string discordId,
        string name,
        string species,
        SkinPresetSkinData skin,
        CancellationToken cancellationToken)
    {
        var now = DateTimeOffset.UtcNow.ToString("O");
        var skinJson = JsonSerializer.Serialize(skin, JsonOptions);

        await using var transaction =
            (SqliteTransaction)await connection.BeginTransactionAsync(
                cancellationToken);

        long presetCount;

        await using (var countCommand = connection.CreateCommand())
        {
            countCommand.Transaction = transaction;
            countCommand.CommandText =
                """
                SELECT COUNT(*)
                FROM skin_presets
                WHERE discord_id = $discordId
                  AND species = $species COLLATE NOCASE;
                """;
            countCommand.Parameters.AddWithValue("$discordId", discordId);
            countCommand.Parameters.AddWithValue("$species", species);
            presetCount = Convert.ToInt64(
                await countCommand.ExecuteScalarAsync(cancellationToken));
        }

        if (presetCount >= MaximumPresetsPerSpecies)
        {
            throw new SkinPresetLimitException(
                species,
                MaximumPresetsPerSpecies);
        }

        var uniqueName = await ResolveUniquePresetNameAsync(
            connection,
            transaction,
            discordId,
            species,
            name,
            cancellationToken);

        await using (var insertCommand = connection.CreateCommand())
        {
            insertCommand.Transaction = transaction;
            insertCommand.CommandText =
                """
                INSERT INTO skin_presets
                (
                    discord_id,
                    species,
                    name,
                    skin_json,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    $discordId,
                    $species,
                    $name,
                    $skinJson,
                    $createdAt,
                    $updatedAt
                );
                """;
            insertCommand.Parameters.AddWithValue("$discordId", discordId);
            insertCommand.Parameters.AddWithValue("$species", species);
            insertCommand.Parameters.AddWithValue("$name", uniqueName);
            insertCommand.Parameters.AddWithValue("$skinJson", skinJson);
            insertCommand.Parameters.AddWithValue("$createdAt", now);
            insertCommand.Parameters.AddWithValue("$updatedAt", now);
            await insertCommand.ExecuteNonQueryAsync(cancellationToken);
        }

        await using var idCommand = connection.CreateCommand();
        idCommand.Transaction = transaction;
        idCommand.CommandText = "SELECT last_insert_rowid();";
        var presetId = Convert.ToInt64(
            await idCommand.ExecuteScalarAsync(cancellationToken));

        var preset = await GetPresetAsync(
            connection,
            transaction,
            discordId,
            presetId,
            cancellationToken);

        await transaction.CommitAsync(cancellationToken);

        return preset
            ?? throw new InvalidOperationException(
                "The saved preset could not be read back.");
    }

    private static async Task<string> ResolveUniquePresetNameAsync(
        SqliteConnection connection,
        SqliteTransaction transaction,
        string discordId,
        string species,
        string requestedName,
        CancellationToken cancellationToken)
    {
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        await using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText =
            """
            SELECT name
            FROM skin_presets
            WHERE discord_id = $discordId
              AND species = $species COLLATE NOCASE;
            """;
        command.Parameters.AddWithValue("$discordId", discordId);
        command.Parameters.AddWithValue("$species", species);

        await using (var reader =
            await command.ExecuteReaderAsync(cancellationToken))
        {
            while (await reader.ReadAsync(cancellationToken))
            {
                names.Add(reader.GetString(0));
            }
        }

        if (!names.Contains(requestedName))
        {
            return requestedName;
        }

        for (var copyNumber = 1; ; copyNumber++)
        {
            var suffix = $"({copyNumber})";
            var stemLength = Math.Max(1, 40 - suffix.Length);
            var stem = requestedName.Length > stemLength
                ? requestedName[..stemLength].TrimEnd()
                : requestedName;
            var candidate = stem + suffix;

            if (!names.Contains(candidate))
            {
                return candidate;
            }
        }
    }

    private static async Task<SkinPresetResponse?> UpdatePresetSkinAsync(
        SqliteConnection connection,
        string discordId,
        long id,
        SkinPresetSkinData skin,
        CancellationToken cancellationToken)
    {
        var now = DateTimeOffset.UtcNow.ToString("O");
        var skinJson = JsonSerializer.Serialize(skin, JsonOptions);

        await using var transaction =
            (SqliteTransaction)await connection.BeginTransactionAsync(
                cancellationToken);

        await using var updateCommand = connection.CreateCommand();
        updateCommand.Transaction = transaction;
        updateCommand.CommandText =
            """
            UPDATE skin_presets
            SET skin_json = $skinJson,
                updated_at = $updatedAt
            WHERE id = $id
              AND discord_id = $discordId;
            """;
        updateCommand.Parameters.AddWithValue("$skinJson", skinJson);
        updateCommand.Parameters.AddWithValue("$updatedAt", now);
        updateCommand.Parameters.AddWithValue("$id", id);
        updateCommand.Parameters.AddWithValue("$discordId", discordId);

        var updated =
            await updateCommand.ExecuteNonQueryAsync(cancellationToken);
        if (updated == 0)
        {
            await transaction.RollbackAsync(cancellationToken);
            return null;
        }

        var preset = await GetPresetAsync(
            connection,
            transaction,
            discordId,
            id,
            cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return preset;
    }

    private static async Task<SkinPresetResponse?> RenamePresetAsync(
        SqliteConnection connection,
        string discordId,
        long id,
        string name,
        CancellationToken cancellationToken)
    {
        await using var transaction =
            (SqliteTransaction)await connection.BeginTransactionAsync(
                cancellationToken);

        string? species = null;

        await using (var speciesCommand = connection.CreateCommand())
        {
            speciesCommand.Transaction = transaction;

            speciesCommand.CommandText =
                """
                SELECT species
                FROM skin_presets
                WHERE id = $id
                  AND discord_id = $discordId
                LIMIT 1;
                """;

            speciesCommand.Parameters.AddWithValue("$id", id);
            speciesCommand.Parameters.AddWithValue(
                "$discordId",
                discordId);

            species = Convert.ToString(
                await speciesCommand.ExecuteScalarAsync(
                    cancellationToken));
        }

        if (string.IsNullOrWhiteSpace(species))
        {
            await transaction.RollbackAsync(cancellationToken);
            return null;
        }

        await using (var duplicateCommand = connection.CreateCommand())
        {
            duplicateCommand.Transaction = transaction;

            duplicateCommand.CommandText =
                """
                SELECT COUNT(*)
                FROM skin_presets
                WHERE discord_id = $discordId
                  AND species = $species COLLATE NOCASE
                  AND name = $name COLLATE NOCASE
                  AND id <> $id;
                """;

            duplicateCommand.Parameters.AddWithValue(
                "$discordId",
                discordId);

            duplicateCommand.Parameters.AddWithValue(
                "$species",
                species);

            duplicateCommand.Parameters.AddWithValue("$name", name);
            duplicateCommand.Parameters.AddWithValue("$id", id);

            var duplicateCount = Convert.ToInt64(
                await duplicateCommand.ExecuteScalarAsync(
                    cancellationToken));

            if (duplicateCount > 0)
            {
                throw new ArgumentException(
                    "Another preset for this species already uses that name.");
            }
        }

        await using (var updateCommand = connection.CreateCommand())
        {
            updateCommand.Transaction = transaction;

            updateCommand.CommandText =
                """
                UPDATE skin_presets
                SET name = $name,
                    updated_at = $updatedAt
                WHERE id = $id
                  AND discord_id = $discordId;
                """;

            updateCommand.Parameters.AddWithValue("$name", name);

            updateCommand.Parameters.AddWithValue(
                "$updatedAt",
                DateTimeOffset.UtcNow.ToString("O"));

            updateCommand.Parameters.AddWithValue("$id", id);

            updateCommand.Parameters.AddWithValue(
                "$discordId",
                discordId);

            await updateCommand.ExecuteNonQueryAsync(
                cancellationToken);
        }

        var preset = await GetPresetAsync(
            connection,
            transaction,
            discordId,
            id,
            cancellationToken);

        await transaction.CommitAsync(cancellationToken);

        return preset;
    }

    private static async Task<bool> DeletePresetAsync(
        SqliteConnection connection,
        string discordId,
        long id,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();

        command.CommandText =
            """
            DELETE FROM skin_presets
            WHERE id = $id
              AND discord_id = $discordId;
            """;

        command.Parameters.AddWithValue("$id", id);
        command.Parameters.AddWithValue("$discordId", discordId);

        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    private static async Task<SkinPresetResponse?> GetOwnedPresetAsync(
        SqliteConnection connection,
        string discordId,
        long id,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();

        command.CommandText =
            """
            SELECT
                id,
                name,
                species,
                skin_json,
                created_at,
                updated_at
            FROM skin_presets
            WHERE id = $id
              AND discord_id = $discordId
            LIMIT 1;
            """;

        command.Parameters.AddWithValue("$id", id);
        command.Parameters.AddWithValue("$discordId", discordId);

        await using var reader =
            await command.ExecuteReaderAsync(cancellationToken);

        return await reader.ReadAsync(cancellationToken)
            ? ReadPreset(reader)
            : null;
    }

    private static async Task<SkinPresetResponse?> GetPresetAsync(
        SqliteConnection connection,
        SqliteTransaction transaction,
        string discordId,
        long id,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.Transaction = transaction;

        command.CommandText =
            """
            SELECT
                id,
                name,
                species,
                skin_json,
                created_at,
                updated_at
            FROM skin_presets
            WHERE id = $id
              AND discord_id = $discordId
            LIMIT 1;
            """;

        command.Parameters.AddWithValue("$id", id);
        command.Parameters.AddWithValue("$discordId", discordId);

        await using var reader =
            await command.ExecuteReaderAsync(cancellationToken);

        return await reader.ReadAsync(cancellationToken)
            ? ReadPreset(reader)
            : null;
    }

    private static SkinPresetResponse ReadPreset(
        SqliteDataReader reader)
    {
        var skin = JsonSerializer.Deserialize<SkinPresetSkinData>(
            reader.GetString(3),
            JsonOptions);

        if (skin is null)
        {
            throw new InvalidOperationException(
                "A saved skin preset contains invalid JSON.");
        }

        return new SkinPresetResponse(
            reader.GetInt64(0),
            reader.GetString(1),
            reader.GetString(2),
            skin,
            reader.GetString(4),
            reader.GetString(5));
    }

    private static string NormalizeName(string? name)
    {
        var normalized = name?.Trim() ?? "";

        if (normalized.Length is < 1 or > 40)
        {
            throw new ArgumentException(
                "Preset names must contain between 1 and 40 characters.");
        }

        if (normalized.Any(char.IsControl))
        {
            throw new ArgumentException(
                "Preset names cannot contain control characters.");
        }

        return normalized;
    }

    private static string NormalizeSpecies(string? species)
    {
        var normalized = species?.Trim() ?? "";

        if (normalized.Length is < 1 or > 64)
        {
            throw new ArgumentException(
                "A valid dinosaur species is required.");
        }

        if (normalized.Any(char.IsControl))
        {
            throw new ArgumentException(
                "The dinosaur species contains invalid characters.");
        }

        return normalized;
    }

    private static string NormalizeCopySpecies(string? species)
    {
        var normalized = species?.Trim() ?? "";

        var canonical = CopySpecies.FirstOrDefault(candidate =>
            string.Equals(
                candidate,
                normalized,
                StringComparison.OrdinalIgnoreCase));

        return canonical
            ?? throw new ArgumentException(
                "Choose a supported dinosaur species.");
    }

    private static SkinPresetSkinData AdaptSkinForSpecies(
        SkinPresetSkinData skin,
        string species)
    {
        var patternCount = PatternCounts.TryGetValue(
            species,
            out var configuredCount)
                ? configuredCount
                : 3;

        return skin with
        {
            PatternIndex = Math.Clamp(
                skin.PatternIndex,
                0,
                Math.Max(0, patternCount - 1))
        };
    }

    private static SkinPresetSkinData NormalizeSkin(
        SkinPresetSkinData? skin)
    {
        if (skin is null)
        {
            throw new ArgumentException("Skin data is required.");
        }

        if (skin.PatternIndex is < 0 or > 20)
        {
            throw new ArgumentException(
                "PatternIndex must be between 0 and 20.");
        }

        if (skin.SkinVariation is < 0 or > 100)
        {
            throw new ArgumentException(
                "SkinVariation must be between 0 and 100.");
        }

        return skin with
        {
            Body = NormalizeColor(skin.Body, nameof(skin.Body)),
            Markings = NormalizeColor(
                skin.Markings,
                nameof(skin.Markings)),
            Flank = NormalizeColor(skin.Flank, nameof(skin.Flank)),
            Underbelly = NormalizeColor(
                skin.Underbelly,
                nameof(skin.Underbelly)),
            Detail1 = NormalizeColor(
                skin.Detail1,
                nameof(skin.Detail1)),
            Eyes = NormalizeColor(skin.Eyes, nameof(skin.Eyes)),
            MaleDisplay = NormalizeColor(
                skin.MaleDisplay,
                nameof(skin.MaleDisplay)),
            Teeth = NormalizeColor(skin.Teeth, nameof(skin.Teeth)),
            Mouth = NormalizeColor(skin.Mouth, nameof(skin.Mouth)),
            Claws = NormalizeColor(skin.Claws, nameof(skin.Claws))
        };
    }

    private static string NormalizeColor(
        string? color,
        string fieldName)
    {
        var normalized = color?.Trim() ?? "";

        if (!ColorPattern.IsMatch(normalized))
        {
            throw new ArgumentException(
                $"{fieldName} must be a six-digit color such as #A1B2C3.");
        }

        return normalized.ToUpperInvariant();
    }

    private static IResult MapException(Exception ex) => ex switch
    {
        UnauthorizedAccessException =>
            Results.Unauthorized(),

        SkinPresetLimitException limit =>
            Results.Json(
                new
                {
                    error = limit.Message,
                    code = "preset-limit-reached",
                    species = limit.Species,
                    limit = limit.Limit
                },
                statusCode: StatusCodes.Status409Conflict),

        ArgumentException argument =>
            Results.BadRequest(new { error = argument.Message }),

        SqliteException sqlite =>
            Results.Json(
                new
                {
                    error = "The skin preset database is unavailable.",
                    detail = sqlite.Message
                },
                statusCode: StatusCodes.Status503ServiceUnavailable),

        IOException io =>
            Results.Json(
                new
                {
                    error = "The skin preset database could not be accessed.",
                    detail = io.Message
                },
                statusCode: StatusCodes.Status503ServiceUnavailable),

        InvalidOperationException invalid =>
            Results.Json(
                new { error = invalid.Message },
                statusCode: StatusCodes.Status503ServiceUnavailable),

        _ =>
            Results.Json(
                new { error = "Unexpected skin preset service error." },
                statusCode: StatusCodes.Status500InternalServerError)
    };
}

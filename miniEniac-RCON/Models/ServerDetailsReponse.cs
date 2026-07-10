namespace miniEniac_RCON.Models;

public class ServerDetailsResponse
{
    public string Name { get; set; } = "";
    public string Map { get; set; } = "";
    public int MaxPlayers { get; set; }
    public int CurrentPlayers { get; set; }

    public bool MutationsEnabled { get; set; }
    public bool HumansEnabled { get; set; }
    public bool PasswordProtected { get; set; }
    public bool QueueEnabled { get; set; }
    public bool WhitelistEnabled { get; set; }
    public bool SpawnAIEnabled { get; set; }

    public int DayLengthMinutes { get; set; }
    public int NightLengthMinutes { get; set; }

    public bool GlobalChatEnabled { get; set; }
}
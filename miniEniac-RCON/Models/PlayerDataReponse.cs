namespace miniEniac_RCON.Models;

public class PlayerDataResponse
{
    public string Name { get; set; } = "";
    public string SteamId { get; set; } = "";
    public string Gender { get; set; } = "";
    public string Class { get; set; } = "";

    public double Growth { get; set; }
    public double Health { get; set; }
    public double Stamina { get; set; }
    public double Hunger { get; set; }
    public double Thirst { get; set; }

    public bool PrimeElder { get; set; }

    public PlayerLocation Location { get; set; } = new();
}


public class PlayerLocation
{
    public double X { get; set; }
    public double Y { get; set; }
    public double Z { get; set; }
}
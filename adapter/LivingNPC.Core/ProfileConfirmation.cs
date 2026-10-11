using System;

namespace LivingNPC.Core
{
    // Game time is not a save identity. Each new session needs the player's selection.
    public sealed class ProfileConfirmation
    {
        private string protagonist = "";
        private DateTime lastGameDate;
        private bool warningShown;
        public bool Confirmed { get; private set; }

        public void Observe(WorldState world, bool reconnect = false)
        {
            if (!world.Safe || reconnect || protagonist != world.ProtagonistId ||
                lastGameDate != default && world.GameDatetime < lastGameDate.AddSeconds(-2))
            { Confirmed = false; warningShown = false; }
            protagonist = world.ProtagonistId; lastGameDate = world.GameDatetime;
        }

        public string Warning(string profileId, string saveSlot, string key)
        {
            warningShown = true;
            if (string.IsNullOrWhiteSpace(profileId) || string.IsNullOrWhiteSpace(saveSlot))
                return "AI stopped: choose ProfileId and SaveSlot in LivingNPC.ini, then reload the adapter. Use a new profile for an unknown or different save.";
            return "Save association warning: verify profile " + profileId + " / slot " + saveSlot + ". Different or unknown save? Edit LivingNPC.ini to use a new profile and reload. Press " + key + " only to confirm this save.";
        }

        public bool Confirm(WorldState world, string profileId, string saveSlot)
        {
            Observe(world);
            if (!world.Safe || !warningShown || string.IsNullOrWhiteSpace(profileId) || string.IsNullOrWhiteSpace(saveSlot)) return false;
            Confirmed = true; return true;
        }
    }
}

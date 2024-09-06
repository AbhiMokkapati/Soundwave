document.getElementById("genreForm").addEventListener("submit", function (event) {
    event.preventDefault();

    const selectedGenre = document.getElementById("genre").value;

    const artistList = document.getElementById("artistList");
    while (artistList.firstChild) {
        artistList.removeChild(artistList.firstChild);
    }

	if (selectedGenre === "Pop") {
        appendArtist("Taylor Swift");
        appendArtist("Dua Lipa");
		appendArtist("Charlie Puth");
        appendArtist("Ed Sheeran");
		appendArtist("Harry Styles");
        appendArtist("Katy Perry");
		appendArtist("The Weeknd");
		appendArtist("Post Malone");

    } else if (selectedGenre === "Rock") {
		appendArtist("The Rolling Stones");
        appendArtist("The Beatles");
		appendArtist("Led Zeppelin");
        appendArtist("Queen");
		appendArtist("AC/DC");
        appendArtist("Coldplay");
		appendArtist("Guns N' Roses");

    } else if (selectedGenre === "Jazz") {
		appendArtist("Miles Davis");
        appendArtist("John Coltrane");
		appendArtist("Ella Fitzgerald");
        appendArtist("Louis Armstrong");
		appendArtist("Duke Ellington");
        appendArtist("Billie Holiday");
		appendArtist("Chet Baker");

	} else if (selectedGenre === "Indie/Alternative") {
		appendArtist("Steve Lacy");
        appendArtist("wave to earth");
		appendArtist("beebadoobee");
        appendArtist("Chase Atlantic");
		appendArtist("Frank Ocean");
        appendArtist("Artic Monkeys");
		appendArtist("Tame Impala");
		appendArtist("Twenty-One Pilots");

    } else if (selectedGenre === "Bollywood") {
		appendArtist("Arijit Singh");
        appendArtist("Shreya Ghoshal");
		appendArtist("Atif Aslam");
        appendArtist("Lata Mangeshkar");
		appendArtist("Sonu Nigam");
        appendArtist("AP Dhillon");
		appendArtist("Asha Bhosle");

    } else if (selectedGenre === "Telugu") {
		appendArtist("Sid Sriram");
        appendArtist("Shreya Ghoshal");
		appendArtist("Devi Sri Prasad");
        appendArtist("Arijit Singh");
		appendArtist("Haricharan");
        appendArtist("S. P. Balasubrahmanyam");
		appendArtist("Geetha Madhuri");

    } else if (selectedGenre === "Spanish") {
		appendArtist("Christian Nodao");
        appendArtist("Karol G");
		appendArtist("Peso Pluma");
        appendArtist("Bad Bunny");
		appendArtist("X");
    } else if (selectedGenre === "KPOP") {
		appendArtist("BTS");
        appendArtist("BLACKPINK");
		appendArtist("EXO");
        appendArtist("TWICE");
		appendArtist("NCT");
        appendArtist("ITZY");
		appendArtist("MAMAMOO");

    } else if (selectedGenre === "Rap") {
		appendArtist("Kendrick Lamar");
        appendArtist("Drake");
		appendArtist("Metro Boomin");
        appendArtist("Eminem");
		appendArtist("Travis Scott");
        appendArtist("21 Savage");
    }
});

function appendArtist(artistName) {
    const artistItem = document.createElement("li");
    artistItem.textContent = artistName;

    document.getElementById("artistList").appendChild(artistItem);
}

document.addEventListener("DOMContentLoaded", function() {

// Select all the elements in the HTML page
// and assign them to a variable
let now_playing = document.querySelector(".now-playing");
let track_name = document.querySelector(".track-name");
let track_artist = document.querySelector(".track-artist");

let playpause_btn = document.querySelector(".playpause-track");
let next_btn = document.querySelector(".next-track");
let prev_btn = document.querySelector(".prev-track");

let seek_slider = document.querySelector(".seek_slider");
let volume_slider = document.querySelector(".volume_slider");
let curr_time = document.querySelector(".current-time");
let total_duration = document.querySelector(".total-duration");

// Specify globally used values
let track_index = 0;
let isPlaying = false;
let updateTimer;

// Create the audio element for the player
let curr_track = document.createElement('audio');

// Define the list of tracks that have to be played
let track_list = [
{
	name: "92 Explorer",
	artist: "Post Malone",
	path: "Songs/1.mp3",
},
{
	name: "Are We Still Friends",
	artist: "Tyler the Creator",
	path: "Songs/2.mp3"
},
{
	name: "Badtameez Dil",
	artist: "Ranbir Kapoor, Deepika Padukone",
	path: "Songs/3.mp3"
},
{
	name: "AMERICA HAS A PROBLEM",
	artist: "Beyonce Ft Kendrick Lamar",
	path: "Songs/4.mp3"
},
{
	name: "Regent's Park",
	artist: "Bruno Major",
	path: "Songs/5.mp3"
},
{
	name: "Blockbuster",
	artist: "Allu Arjun, Rakul Preet Singh, Catherine Tresa",
	path: "Songs/6.mp3"
},
{
	name: "Bom Diggy",
	artist: "Zack Knight, Jasmin Walia",
	path: "Songs/7.mp3"
},
{
	name: "Grenade",
	artist: "Bruno Mars",
	path: "Songs/8.mp3"
},
{
	name: "Fly As Me",
	artist: "Bruno Mars, Anderson .Paak, Silk Sonic",
	path: "Songs/9.mp3"
},
{
	name: "Clocks",
	artist: "Coldplay",
	path: "Songs/10.mp3"
},
{
	name: "Always",
	artist: "Daniel Caesar",
	path: "Songs/11.mp3"
},
{
	name: "Don't Blame Me",
	artist: "Taylor Swift",
	path: "Songs/12.mp3"
},
{
	name: "In the Bible",
	artist: "Drake, Lil Durk, Giveon",
	path: "Songs/13.mp3"
},
{
	name: "ghosts",
	artist: "highvyn",
	path: "Songs/14.mp3"
},
{
	name: "Cherry Wine",
	artist: "grentperez",
	path: "Songs/15.mp3"
},
{
	name: "Coast",
	artist: "Hailee Steinfeld ft. Anderson .Paak",
	path: "Songs/16.mp3"
},
{
	name: "Cinema",
	artist: "Harry Styles",
	path: "Songs/17.mp3"
},
{
	name: "ANGEL",
	artist: "keshi",
	path: "Songs/18.mp3"
},
{
	name: "Poker Face",
	artist: "Lady Gaga",
	path: "Songs/19.mp3"
},
{
	name: "From the Start",
	artist: "Laufey",
	path: "Songs/20.mp3"
},
{
	name: "Laychalo",
	artist: "Rakul Preet Singh",
	path: "Songs/21.mp3"
},
{
	name: "Lovesong",
	artist: "beebadoobee",
	path: "Songs/22.mp3"
},
{
	name: "Nice for What",
	artist: "Drake",
	path: "Songs/23.mp3"
},
{
	name: "Hollywood's Bleeding",
	artist:  "Post Malone",
	path: "Songs/24.mp3"
},
{
	name: "Rockstar",
	artist: "Post Malone ft 21 Savage",
	path: "Songs/25.mp3"
},
{
	name: "Reminder",
	artist: "The Weeknd",
	path: "Songs/26.mp3"
},
{
	name: "Starboy",
	artist: "The Weeknd",
	path: "Songs/27.mp3"
},
{
	name: "I Hate You",
	artist: "SZA",
	path: "Songs/28.mp3"
},
{
	name: "Cruel Summer",
	artist: "Taylor Swift",
	path: "Songs/29.mp3"
},
{
	name: "Too Many Nights",
	artist: "Metro Boomin & Future",
	path: "Songs/30.mp3"
},
{
	name: "Hurts Me",
	artist: "Tory Lanez & Trippie Redd",
	path: "Songs/31.mp3"
},
{
	name: "MY EYES",
	artist: "Travis Scott",
	path: "Songs/32.mp3"
},
{
	name: "Wants and Needs",
	artist: "Drake",
	path: "Songs/33.mp3"
},
{
	name: "impossible",
	artist: "Wasia Project",
	path: "Songs/34.mp3"
},
{
	name: "bad",
	artist: "wave to earth",
	path: "Songs/35.mp3"
},
];

function playpauseTrack() {
    // Switch between playing and pausing
    // depending on the current state
    if (!isPlaying) playTrack();
    else pauseTrack();
    }
    
function playTrack() {
    // Play the loaded track
    curr_track.play();
    isPlaying = true;
    
    // Replace icon with the pause icon
    playpause_btn.innerHTML = '<i class="fa fa-pause-circle fa-5x"></i>';
    }
    
function pauseTrack() {
    // Pause the loaded track
    curr_track.pause();
    isPlaying = false;
    
    // Replace icon with the play icon
    playpause_btn.innerHTML = '<i class="fa fa-play-circle fa-5x"></i>';
    }
    
function nextTrack() {
    // Go back to the first track if the
    // current one is the last in the track list
    if (track_index < track_list.length - 1)
        track_index += 1;
    else track_index = 0;
    
    // Load and play the new track
    loadTrack(track_index);
    playTrack();
    }
    
function prevTrack() {
    // Go back to the last track if the
    // current one is the first in the track list
    if (track_index > 0)
        track_index -= 1;
    else track_index = track_list.length - 1;
    
    // Load and play the new track
    loadTrack(track_index);
    playTrack();
    }

    function seekTo() {
        // Calculate the seek position by the
        // percentage of the seek slider 
        // and get the relative duration to the track
        seekto = curr_track.duration * (seek_slider.value / 100);
        
        // Set the current track position to the calculated seek position
        curr_track.currentTime = seekto;
        }
        
        function setVolume() {
        // Set the volume according to the
        // percentage of the volume slider set
        curr_track.volume = volume_slider.value / 100;
        }
        
        function seekUpdate() {
        let seekPosition = 0;
        
        // Check if the current track duration is a legible number
        if (!isNaN(curr_track.duration)) {
            seekPosition = curr_track.currentTime * (100 / curr_track.duration);
            seek_slider.value = seekPosition;
        
            // Calculate the time left and the total duration
            let currentMinutes = Math.floor(curr_track.currentTime / 60);
            let currentSeconds = Math.floor(curr_track.currentTime - currentMinutes * 60);
            let durationMinutes = Math.floor(curr_track.duration / 60);
            let durationSeconds = Math.floor(curr_track.duration - durationMinutes * 60);
        
            // Add a zero to the single digit time values
            if (currentSeconds < 10) { currentSeconds = "0" + currentSeconds; }
            if (durationSeconds < 10) { durationSeconds = "0" + durationSeconds; }
            if (currentMinutes < 10) { currentMinutes = "0" + currentMinutes; }
            if (durationMinutes < 10) { durationMinutes = "0" + durationMinutes; }
        
            // Display the updated duration
            curr_time.textContent = currentMinutes + ":" + currentSeconds;
            total_duration.textContent = durationMinutes + ":" + durationSeconds;
        }
        }

// Load the first track in the tracklist
loadTrack(track_index);

function loadTrack(trackIndex) {
    // Load the track based on the provided index
    curr_track.src = track_list[trackIndex].path;
    curr_track.load();
  
    // Update the details of the currently playing track
    track_name.textContent = track_list[trackIndex].name;
    track_artist.textContent = track_list[trackIndex].artist;
    now_playing.textContent = `PLAYING ${trackIndex + 1} OF ${track_list.length}`;
  
    // Reset the seek slider and update the volume slider
    seek_slider.value = 0;
    volume_slider.value = 99; // You may want to set it to a default value
  }
  
  // Add event listeners to your buttons
  playpause_btn.addEventListener("click", playpauseTrack);
  next_btn.addEventListener("click", nextTrack);
  prev_btn.addEventListener("click", prevTrack);
  seek_slider.addEventListener("change", seekTo);
  volume_slider.addEventListener("change", setVolume);
  
  // Add an event listener for updating the seek position
  curr_track.addEventListener("timeupdate", seekUpdate);
  

});   

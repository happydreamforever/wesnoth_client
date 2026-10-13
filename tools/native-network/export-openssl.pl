# Emit only portable upstream library graph data; never run a compiler.
use strict;
use warnings;
use JSON::PP;
use configdata;
if (@ARGV && $ARGV[0] eq '--generated') {
    print "$_\n" for sort grep { /\.(c|h)$/ } keys %{$unified_info{generate}};
    exit 0;
}
my %output;
for my $library ('libcrypto', 'libssl') {
    my (%seen, %sources, %includes, %defines);
    my $visit;
    $visit = sub {
        my ($name) = @_;
        return if $seen{$name}++;
        if ($name =~ /\.c$/) { $sources{$name} = 1; return; }
        $includes{$_} = 1 for @{$unified_info{includes}{$name} || []};
        $defines{$_} = 1 for @{$unified_info{defines}{$name} || []};
        $visit->($_) for @{$unified_info{sources}{$name} || []};
        for (@{$unified_info{depends}{$name} || []}) { $visit->($_) if /\.a$/; }
    };
    $visit->($library);
    $output{$library} = { sources => [sort keys %sources], includes => [sort keys %includes], defines => [sort keys %defines] };
}
print JSON::PP->new->canonical->pretty->encode(\%output);
